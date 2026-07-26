# Detect Objects on Camera

The **Detect Objects on Camera** example lets you detect objects on a live feed and visualize bounding boxes around the detections in real-time.

By default the video source is a **USB camera** (the original behavior). Optionally,
it can instead pull its feed from an **Android phone running an IP-camera app**
(e.g. "IP Webcam" by Pavel Khlebovich) over the network, for testing without a USB
camera attached.

![Detect Objects on Camera](assets/docs_assets/video-object-detection.png)

This example uses a pre-trained model to detect objects on a live video feed. The
workflow involves continuously getting frames from the camera, processing them
through an AI model using the `video_objectdetection` Brick, and displaying the
bounding boxes around detections. The App is managed from an interactive web
interface.

## Camera Source

Controlled by `CAMERA_SOURCE` — switching between USB, IP camera, and video file is
purely a `CAMERA_SOURCE` change, no `app.yaml` edits needed. In every mode the app
itself opens the camera (`V4LCamera`, `IPCamera`, or the local `FileCamera`) and
hands it to `VideoObjectDetection`, which captures from it and forwards frames to
the detection runner; the runner never needs direct device access, so `app.yaml`
declares `devices: [remote_camera_0]` on the `video_object_detection` brick
unconditionally.

| Var | Default | Meaning |
|-----|---------|---------|
| `CAMERA_SOURCE` | `file` | `usb` for a physically-connected USB camera, `ip` for an Android IP-camera stream, or `file` to loop a local video file instead of a live feed |

### USB (default)

**Note:** This mode must be run in **Network Mode** in the Arduino App Lab, since it
requires a USB-C hub and a USB camera.

| Var | Default | Meaning |
|-----|---------|---------|
| `USB_CAMERA_DEVICE` | _(from `VIDEO_DEVICE`, else `0`)_ | USB camera index or `/dev/videoX` path. `arduino-app-cli` auto-detects a connected camera into `VIDEO_DEVICE`; only set this to override that. |

Requires the camera to expose a `/dev/v4l/by-id/` udev entry (standard for real USB
webcams). On hardware with no physical camera attached at all, this fails at
startup with `CameraOpenError` rather than the CLI's pre-flight check — expected,
since `app.yaml` no longer declares a hard physical-camera requirement.

### IP camera (optional)

1. On the Android phone, install an IP-camera app (e.g. "IP Webcam") and start its
   server. Make sure the phone is on the **same network** as the board.
2. Note the stream URL it shows, typically `http://<phone-ip>:8080/video`.
3. Set `CAMERA_SOURCE=ip`, `IP_CAMERA_URL` (and `IP_CAMERA_USERNAME` /
   `IP_CAMERA_PASSWORD` if the app's stream is password-protected) as below.

| Var | Default | Meaning |
|-----|---------|---------|
| `IP_CAMERA_URL` | `http://192.168.18.65:8080/video` | IP camera stream URL (RTSP/HTTP/HTTPS) |
| `IP_CAMERA_USERNAME` | _(none)_ | Optional stream auth username |
| `IP_CAMERA_PASSWORD` | _(none)_ | Optional stream auth password |
| `IP_CAMERA_FPS` | `10` | Frames per second to pull from the stream |

### Video file (optional)

Loops a local video file as the detection input — useful for testing/demoing
without any camera attached. The file must be reachable inside the app container;
place it somewhere under the app folder (e.g. `media/`, next to `python/`), which
is bind-mounted to `/app`, and point `VIDEO_FILE_PATH` at its in-container path.

A sample clip is bundled at `media/sample.mp4` (stock footage from Pexels, free
license) — set `VIDEO_FILE_PATH=/app/media/sample.mp4` to try this mode out of
the box.

| Var | Default | Meaning |
|-----|---------|---------|
| `VIDEO_FILE_PATH` | `/app/media/sample.mp4` | Path to the video file, as seen inside the container |
| `VIDEO_FILE_LOOP` | `true` | Rewind and replay the file once it ends, so detection keeps running continuously |
| `VIDEO_FILE_FPS` | `10` | Frames per second to read from the file |

## Brick Used

The example uses the following Bricks:

- `web_ui`: Brick to create a web interface to display the classification results and model controls.
- `video_objectdetection`: Brick to classify objects within a live video feed from a camera.

## Qonclave Hub Event Forwarding

When a `person` is detected with confidence above a configurable threshold, this app
POSTs the detection frame + event metadata to the Qonclave hub's `/edge/event`
endpoint (see `hub/README.md`), so the hub can run its own heavier verification. A
hysteresis window prevents re-sending on every frame while a person stays in view.

Configurable via environment variables (set as Brick Configuration / env vars in
App Lab):

| Var | Default | Meaning |
|-----|---------|---------|
| `DEVICE_ID` | `unoq-01` | Identifier sent as `device_id` in the event |
| `HUB_IP` | `192.168.18.62` | Hub's IP address |
| `HUB_PORT` | `8000` | Hub's HTTP port |
| `PERSON_CONFIDENCE_THRESHOLD` | `0.7` | Minimum person-detection confidence (0-1) to trigger an event |
| `HUB_EVENT_HYSTERESIS_SEC` | `10` | Minimum seconds between two hub events |
| `HUB_EVENT_TIMEOUT_SEC` | `5` | HTTP request timeout when posting to the hub |

## Hardware and Software Requirements

### Hardware

- [Arduino® UNO Q](https://store.arduino.cc/products/uno-q) or Arduino VENTUNO Q
- USB camera (x1) — _or_ an Android phone running an IP-camera app on the same network (see Camera Source above)
- USB-C® hub adapter with external power (x1) _(only for UNO Q, only in USB camera mode)_
- A power supply (5 V, 3 A) for the USB hub (e.g. a phone charger) _(only for UNO Q, only in USB camera mode)_
- Potentiometer connected to analog pin A0 (optional, for physical threshold control)
- Personal computer with internet access

### Software

- Arduino App Lab

## How to Use the Example

1. USB mode (default): connect the USB-C hub to the UNO Q and the USB camera, then
   attach the external power supply.
   ![Hardware setup](assets/docs_assets/hardware-setup.png)
   IP camera mode: start the IP-camera app on the phone and set `CAMERA_SOURCE=ip` /
   `IP_CAMERA_URL` as described in Camera Source above.
2. (Optional) Connect a potentiometer wiper to analog pin A0 on the UNO Q for physical confidence threshold control.
3. Run the App.
   ![Arduino App Lab - Run App](assets/docs_assets/launch-app.png)
4. The App should open automatically in the web browser. You can open it manually via `<board-name>.local:7000`.
5. Position any object in front of the camera and watch as the App detects and recognizes them.
6. Observe the UNO Q's onboard **12x8 LED Matrix**: it will dynamically render custom hardware-aligned icons corresponding to detected objects!
7. Rotate the potentiometer knob to dynamically adjust the AI detection confidence threshold in real-time.

Try with one of the following objects for a special reaction (both on the Web UI and the LED Matrix):

- Cat
- Cell phone
- Clock
- Cup
- Dog
- Potted plant
- Person

![Example of special reaction](assets/docs_assets/special-detection.png)

## How it Works

This example hosts a Web UI where we can see the video input from the camera connected via USB. The video stream is then processed using the `video_objectdetection` Brick. When an object is detected, it is logged along with the confidence score (e.g. 95% potted plant).

Here is a brief explanation of the full-stack application:

### 🔧 Backend (main.py)

- Initializes the app Bricks:
  - **WebUI** (`ui = WebUI()`): channel to push messages to the frontend.
  - **VideoObjectDetection** (`detection_stream = VideoObjectDetection()`): runs object detection on the video stream.

- Wires detection events to actions using callbacks:
  - `on_detect_all(send_detections_to_ui)`: sends `{ content, confidence, timestamp }` via `ui.send_message("detection", ...)`

- **Controls**:
  - Listens for `override_th` → updates detection threshold

- Exposes:
  - **Realtime messaging**: publishes detection updates to the frontend via `ui.send_message("detection", message=entry)` so the UI can display live detections.

- Runs with `App.run()` which starts the internal event loop and keeps the detection stream and UI messaging alive.

---

### ⚡ Microcontroller & Hardware Bridge (sketch/sketch.ino)

- **12x8 LED Matrix Icon Display**:
  - Uses the `Arduino_LED_Matrix` library configured with a **13-column hardware stride buffer** (`byte frame[8][13]`) required by the UNO Q and Zephyr OS architecture.
  - Receives `set_led_state` commands over the Bridge from `main.py` (e.g. `"cat"`, `"person"`, `"cell phone"`, `"green"`, `"clear"`) and renders hardware-aligned bitmap icons in real-time.
- **Physical Potentiometer Threshold Control**:
  - Continuously samples analog pin A0 (`analogRead(A0)`) with debounce and noise filtering.
  - When the knob is adjusted, it transmits percentage changes over the Bridge via `Bridge.call("on_knob_change", str(percentage))` to dynamically adjust the AI detection confidence threshold in Python and update the Web UI slider simultaneously.

---

### 💻 Frontend (index.html + app.js)

- **Video feed**
  - iframe auto-retries /embed until the camera stream is available

- **Controls**
  - Slider, numeric input, and reset button adjust threshold live
  - Updates sent to backend with: `ui.send_message("override_th", value)`

- **Feedback**
  - Shows GIF + text for known objects (dog, cat, cup, cell phone, clock, potted plant)

- **Recent detections**
  - Displays the last 5 detections with percentage and timestamp

- **Connection status**
  - Shows an error message if the WebSocket connection drops

---

## Understanding the Code

Once the application is running, you can open it in your browser by navigating to `<BOARD-IP-ADDRESS>:7000`.  
At that point, the device begins performing the following:

- Serving the **object detection UI** and exposing realtime transports.

  The UI is hosted by the `WebUI` Brick and communicates with the backend via WebSocket.  
   The backend pushes detection messages whenever new objects are found.

  ```python
  from arduino.app_bricks.web_ui import WebUI
  from arduino.app_bricks.video_objectdetection import VideoObjectDetection
  from datetime import datetime, UTC

  ui = WebUI()
  detection_stream = VideoObjectDetection()

  ui.on_message("override_th",
                lambda sid, threshold: detection_stream.override_threshold(threshold))

  detection_stream.on_detect_all(send_detections_to_ui)
  ```

  - `detection` (WebSocket message): JSON entry with label, confidence, and timestamp sent to the UI.
  - `override_th` (WebSocket → backend): adjusts the confidence threshold live.

- Processing detections and broadcasting updates.

  When the model detects objects, the backend:
  1. Iterates over all detected objects with their confidence scores.
  2. Attaches an ISO 8601 UTC timestamp.
  3. Publishes each detection as a JSON entry to the frontend channel `detection`.

  ```python
  def send_detections_to_ui(detections: dict):
      for key, value in detections.items():
          entry = {
              "content": key,
              "confidence": value,
              "timestamp": datetime.now(UTC).isoformat()
          }
          ui.send_message("detection", message=entry)
  ```

- Rendering and interacting on the frontend.

  The **index.html + app.js** bundle defines the interface:
  - A **video feed iframe** auto-retries `/embed` until the camera stream is live.
  - A **confidence control** (slider + input + reset) lets the user adjust the detection threshold.
  - A **feedback section** shows animations and messages for known classes (cat, dog, cup, clock, potted plant, etc.).
  - A **recent detections list** displays the latest 5 detections with percentage and timestamp.

  ```javascript
  const ui = new WebUI();

  ui.on_message('detection', (message) => {
    printDetection(message); // update history
    renderDetections(); // redraw the list
    updateFeedback(message); // update feedback panel
  });
  ```

  - `detection` (WebSocket): received whenever the backend publishes results.
  - The slider and input dynamically update the backend threshold (`override_th`).
  - If the connection drops, an error banner is shown (`error-container`).

- Executing the event loop.

  Finally, the backend keeps everything alive with:

  ```python
  App.run()
  ```

  This maintains the object detection stream, callback hooks, threshold overrides, and WebSocket communication with the frontend.
