# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

import os
import threading
import time
import uuid
from datetime import datetime, UTC

import requests

from arduino.app_utils import App, Logger
from arduino.app_bricks.web_ui import WebUI
from arduino.app_bricks.video_objectdetection import VideoObjectDetection
from arduino.app_peripherals.camera import IPCamera

log = Logger("qonclave.edge")

# --- Camera source: USB webcam by default, or an Android IP-camera app ---
# (e.g. "IP Webcam") when CAMERA_SOURCE=ip. Switching to "ip" also requires
# adding `devices: [remote_camera_0]` under this app's video_object_detection
# brick in app.yaml -- see edge/README.md.
CAMERA_SOURCE = os.environ.get("CAMERA_SOURCE", "usb").strip().lower()

camera = None
if CAMERA_SOURCE == "ip":
  IP_CAMERA_URL = os.environ.get("IP_CAMERA_URL", "http://192.168.18.65:8080/video")
  IP_CAMERA_USERNAME = os.environ.get("IP_CAMERA_USERNAME") or None
  IP_CAMERA_PASSWORD = os.environ.get("IP_CAMERA_PASSWORD") or None
  IP_CAMERA_FPS = int(os.environ.get("IP_CAMERA_FPS", "10"))
  camera = IPCamera(url=IP_CAMERA_URL, username=IP_CAMERA_USERNAME,
                    password=IP_CAMERA_PASSWORD, fps=IP_CAMERA_FPS)

ui = WebUI()
detection_stream = VideoObjectDetection(camera, confidence=0.5, debounce_sec=0.0, camera_preview=True)

ui.on_message("override_th", lambda sid, threshold: detection_stream.override_threshold(threshold))

# --- Hub event forwarding: notify the Qonclave hub when a person is detected ---

DEVICE_ID = os.environ.get("DEVICE_ID", "unoq-01")
HUB_IP = os.environ.get("HUB_IP", "192.168.18.62")
HUB_PORT = int(os.environ.get("HUB_PORT", "8000"))
PERSON_CONFIDENCE_THRESHOLD = float(os.environ.get("PERSON_CONFIDENCE_THRESHOLD", "0.7"))
HUB_EVENT_HYSTERESIS_SEC = float(os.environ.get("HUB_EVENT_HYSTERESIS_SEC", "10"))
HUB_EVENT_TIMEOUT_SEC = float(os.environ.get("HUB_EVENT_TIMEOUT_SEC", "5"))

_hub_event_lock = threading.Lock()
_last_hub_event_at = 0.0


def _post_person_event(confidence: float, frame: bytes):
  url = f"http://{HUB_IP}:{HUB_PORT}/edge/event"
  params = {
    "device_id": DEVICE_ID,
    "event_id": f"{DEVICE_ID}-{uuid.uuid4().hex[:8]}",
    "event_type": "person_detected",
    "edge_model": "video_object_detection",
    "edge_confidence": confidence,
  }
  try:
    resp = requests.post(url, params=params, data=frame,
                         headers={"Content-Type": "image/jpeg"},
                         timeout=HUB_EVENT_TIMEOUT_SEC)
    log.info(f"Hub event sent (person, confidence={confidence:.2f}) -> {resp.status_code} {resp.text[:200]}")
  except requests.RequestException as e:
    log.error(f"Failed to send hub event to {url}: {e}")


def maybe_notify_hub(detections: dict, frame: bytes | None):
  if not frame:
    return

  person_detections = detections.get("person", [])
  if not person_detections:
    return

  best_confidence = max(d.get("confidence", 0.0) for d in person_detections)
  if best_confidence <= PERSON_CONFIDENCE_THRESHOLD:
    return

  global _last_hub_event_at
  with _hub_event_lock:
    now = time.monotonic()
    if now - _last_hub_event_at < HUB_EVENT_HYSTERESIS_SEC:
      return
    _last_hub_event_at = now

  threading.Thread(target=_post_person_event, args=(best_confidence, frame), daemon=True).start()


# Register a callback for when all objects are detected
def send_detections_to_ui(detections: dict, frame: bytes | None = None):
  for key, values in detections.items():
    for value in values:
      entry = {
        "content": key,
        "confidence": value.get("confidence"),
        "timestamp": datetime.now(UTC).isoformat()
      }
      ui.send_message("detection", message=entry)

  maybe_notify_hub(detections, frame)

detection_stream.on_detect_all(send_detections_to_ui)

App.run()
