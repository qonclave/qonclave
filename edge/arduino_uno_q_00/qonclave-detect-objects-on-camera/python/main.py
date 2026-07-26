# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

import os
import threading
import time
import uuid
from datetime import datetime, UTC

import requests

from arduino.app_utils import App, Logger, Bridge
from arduino.app_bricks.web_ui import WebUI
from arduino.app_bricks.video_objectdetection import VideoObjectDetection
from arduino.app_bricks.cloud_llm import CloudLLM
from arduino.app_peripherals.camera import IPCamera, V4LCamera

import json

from file_camera import FileCamera

log = Logger("qonclave.edge")

# Load persistent 12x8 LED matrix icons cache
CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "icons_cache.json")
icon_cache = {}
try:
  if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
      icon_cache = json.load(f)
    log.info(f"Loaded {len(icon_cache)} icons from cache.")
except Exception as e:
  log.warning(f"Could not load icons cache: {e}")

llm = CloudLLM()
_generating_labels = set()
_llm_lock = threading.Lock()

def _save_cache():
  try:
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
      json.dump(icon_cache, f, indent=2)
  except Exception as e:
    log.warning(f"Failed to save icons cache: {e}")

def _generate_icon_thread(label: str):
  log.info(f"Generating 10x6 LED matrix icon via CloudLLM for: {label}")
  prompt = (
      f"You are an expert icon designer for an LED matrix. Given the object name '{label}', "
      "output ONLY a JSON array containing exactly 6 lists, each with exactly 10 integers (0 for OFF, 1 for ON), "
      "forming a recognizable centered silhouette of the object. Do not include markdown formatting, backticks, or any text other than the JSON array."
  )
  try:
    resp = llm.generate(prompt) if hasattr(llm, "generate") else llm.chat(prompt)
    resp_text = resp.text if hasattr(resp, "text") else (resp if isinstance(resp, str) else str(resp))
    resp_text = resp_text.replace("```json", "").replace("```", "").strip()
    grid_10x6 = json.loads(resp_text)

    if isinstance(grid_10x6, list) and len(grid_10x6) == 6 and all(isinstance(r, list) and len(r) == 10 for r in grid_10x6):
      # Wrap in 1-pixel empty outer border (10x6 -> 12x8)
      full_grid = [[0] * 12]
      for row in grid_10x6:
        full_grid.append([0] + [1 if x else 0 for x in row] + [0])
      full_grid.append([0] * 12)

      with _llm_lock:
        icon_cache[label] = full_grid
        _save_cache()
      log.info(f"Successfully generated and cached AI icon for '{label}'")

      # Push new icon immediately to hardware and Web UI
      bitstring = "".join("1" if val else "0" for r in full_grid for val in r[:12])
      Bridge.call("set_custom_led_array", bitstring)
      ui.send_message("sync_icons", message=icon_cache)
      ui.send_message("led_status", message={"state": "active", "trigger": label, "bitmap": full_grid, "ai_generated": True})
  except Exception as e:
    log.error(f"LLM icon generation failed for '{label}': {e}")
  finally:
    with _llm_lock:
      _generating_labels.discard(label)

def get_or_trigger_icon(label: str):
  if not label:
    return icon_cache.get("clear"), False
  label = label.lower().strip()
  with _llm_lock:
    if label in icon_cache:
      return icon_cache[label], False
    if label not in _generating_labels:
      _generating_labels.add(label)
      threading.Thread(target=_generate_icon_thread, args=(label,), daemon=True).start()
  return icon_cache.get("clear"), True

# --- Camera source: a bundled sample video file by default (no hardware ---
# needed), a physically-connected USB webcam when CAMERA_SOURCE=usb, or an
# Android IP-camera app (e.g. "IP Webcam") when CAMERA_SOURCE=ip. All paths
# construct the camera ourselves and hand it to VideoObjectDetection, which
# captures from it and forwards frames to the detection runner -- so
# app.yaml's `devices: [remote_camera_0]` on the video_object_detection
# brick stays the same for every mode (the runner never needs direct device
# access; the USB device itself is opened here, in the main container, which
# always has /dev access).
CAMERA_SOURCE = os.environ.get("CAMERA_SOURCE", "usb").strip().lower()

if CAMERA_SOURCE == "ip":
  IP_CAMERA_URL = os.environ.get("IP_CAMERA_URL", "http://192.168.18.65:8080/video")
  IP_CAMERA_USERNAME = os.environ.get("IP_CAMERA_USERNAME") or None
  IP_CAMERA_PASSWORD = os.environ.get("IP_CAMERA_PASSWORD") or None
  IP_CAMERA_FPS = int(os.environ.get("IP_CAMERA_FPS", "10"))
  camera = IPCamera(url=IP_CAMERA_URL, username=IP_CAMERA_USERNAME,
                    password=IP_CAMERA_PASSWORD, fps=IP_CAMERA_FPS)
elif CAMERA_SOURCE == "file":
  VIDEO_FILE_PATH = os.environ.get("VIDEO_FILE_PATH", "/app/media/sample.mp4")
  if not VIDEO_FILE_PATH:
    raise RuntimeError("CAMERA_SOURCE=file requires VIDEO_FILE_PATH to be set")
  VIDEO_FILE_LOOP = os.environ.get("VIDEO_FILE_LOOP", "true").strip().lower() not in ("0", "false", "no")
  VIDEO_FILE_FPS = int(os.environ.get("VIDEO_FILE_FPS", "10"))
  camera = FileCamera(VIDEO_FILE_PATH, loop=VIDEO_FILE_LOOP, fps=VIDEO_FILE_FPS)
else:
  # VIDEO_DEVICE is set by arduino-app-cli when it detects a local USB
  # camera; USB_CAMERA_DEVICE lets that be overridden explicitly.
  USB_CAMERA_DEVICE = os.environ.get("USB_CAMERA_DEVICE") or os.environ.get("VIDEO_DEVICE", 0)
  camera = V4LCamera(device=USB_CAMERA_DEVICE)

ui = WebUI()
ui.on_message("request_icons", lambda sid, data: ui.send_message("sync_icons", message=icon_cache))
detection_stream = VideoObjectDetection(camera, confidence=0.5, debounce_sec=0.0, camera_preview=True)

ui.on_message("override_th", lambda sid, threshold: detection_stream.override_threshold(threshold))

# 1. Listen for Potentiometer Knob adjustments from MCU (sketch.ino)
def handle_knob_change(percentage_str):
  try:
    val = float(percentage_str) / 100.0
    detection_stream.override_threshold(val)
    ui.send_message("knob_update", message={"threshold": val})
  except Exception:
    pass

Bridge.provide("on_knob_change", handle_knob_change)
green_bmp = icon_cache.get("green")
if green_bmp:
  Bridge.call("set_custom_led_array", "".join("1" if val else "0" for r in green_bmp for val in r[:12]))

# --- Hub event forwarding: notify the Qonclave hub when a person is detected ---

DEVICE_ID = os.environ.get("DEVICE_ID", "unoq-01")
HUB_IP = os.environ.get("HUB_IP", "192.168.18.68")
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
  if detections:
    first_obj = list(detections.keys())[0]
    bitmap, is_generating = get_or_trigger_icon(first_obj)
    bitstring = "".join("1" if val else "0" for r in bitmap for val in r[:12]) if bitmap else "0" * 96
    Bridge.call("set_custom_led_array", bitstring)
    ui.send_message("led_status", message={"state": "active", "trigger": first_obj, "bitmap": bitmap, "ai_generated": (first_obj not in ["person", "cat", "dog", "cell phone", "clock", "cup", "potted plant", "clear", "green"])})
  else:
    clear_bmp = icon_cache.get("clear")
    bitstring = "".join("1" if val else "0" for r in clear_bmp for val in r[:12]) if clear_bmp else "0" * 96
    Bridge.call("set_custom_led_array", bitstring)
    ui.send_message("led_status", message={"state": "clear", "trigger": "clear", "bitmap": clear_bmp})

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
