# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

import json
import os
import threading
import time
import uuid
from datetime import datetime, UTC

import socket
import requests
from dotenv import load_dotenv
from starlette.responses import HTMLResponse, StreamingResponse

from arduino.app_utils import App, Logger, Bridge
from arduino.app_bricks.web_ui import WebUI
from arduino.app_bricks.video_objectdetection import VideoObjectDetection
from arduino.app_peripherals.camera import IPCamera, V4LCamera

import json
from basic_auth import BasicAuthMiddleware
from file_camera import FileCamera
from follow_target_selector import FollowTargetSelector
from identity_map import IdentityMap
from investigation_approach import describe as describe_approach, plan_approach
from led_display import person_display_bitmap
from mqtt_client import EdgeMQTTClient
from analysis_client import AnalysisClient
from person_centering import PersonCenteringController, horizontal_bearing_degrees
from person_distance import PersonDistanceController, size_ratio_of
from person_tracker import PersonTracker
from priority_sync import PriorityMapClient
from track_crop import crop_persons, remove_crop_locally, save_crop_locally
from track_overlay import draw_track_overlay_bgr, encode_jpeg

load_dotenv()

log = Logger("qonclave.edge")

DEVICE_ID = os.environ.get("DEVICE_ID", "unoq-01")
HUB_DISCOVERY_ENABLED = os.environ.get("HUB_DISCOVERY_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
HUB_MDNS_NAME = os.environ.get("HUB_MDNS_NAME", "qonclave-hub.local").strip()
HUB_IP = os.environ.get("HUB_IP", "192.168.18.62").strip()
HUB_PORT = int(os.environ.get("HUB_PORT", "8000"))

# --- Hub->edge command channel (MQTT) -------------------------------------
# The MQTT broker runs on the hub machine, so the edge connects to the hub's
# host by default; MQTT_HOST overrides it (e.g. a standalone broker). The hub
# publishes commands to qonclave/<device_id>/command.
MQTT_ENABLED = os.environ.get("EDGE_MQTT_ENABLED", "1") == "1"
MQTT_HOST = os.environ.get("MQTT_HOST", HUB_IP).strip()
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
TTL_SECONDS = 1800.0  # 30 minutes
TTL_SECONDS = 1800.0  # 30 minutes

_resolved_hub_host = None
_discovery_method = "Searching..." if HUB_DISCOVERY_ENABLED else "Static IP (Discovery Disabled)"
_hub_online = False

def get_hub_base_url() -> str:
  global _resolved_hub_host, _discovery_method
  if not HUB_DISCOVERY_ENABLED:
    _resolved_hub_host = HUB_IP
    _discovery_method = "Static IP (Discovery Disabled)"
    return f"http://{_resolved_hub_host}:{HUB_PORT}"

  if _resolved_hub_host:
    return f"http://{_resolved_hub_host}:{HUB_PORT}"
  if HUB_MDNS_NAME:
    try:
      socket.gethostbyname(HUB_MDNS_NAME)
      _resolved_hub_host = HUB_MDNS_NAME
      _discovery_method = "mDNS (Option B)"
      log.info(f"Resolved Hub via mDNS (Option B): {_resolved_hub_host}")
      return f"http://{_resolved_hub_host}:{HUB_PORT}"
    except Exception:
      log.debug(f"mDNS resolution for '{HUB_MDNS_NAME}' failed; attempting UDP LAN discovery...")

  # Attempt UDP LAN broadcast discovery on port 8888
  try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
      sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
      sock.settimeout(1.5)
      sock.sendto(json.dumps({"probe": "qonclave-hub"}).encode("utf-8"), ("255.255.255.255", 8888))
      data, addr = sock.recvfrom(1024)
      msg = json.loads(data.decode("utf-8", errors="ignore"))
      if isinstance(msg, dict) and msg.get("service") == "qonclave-hub":
        _resolved_hub_host = addr[0]
        _discovery_method = "UDP Broadcast"
        log.info(f"Discovered Qonclave Hub via UDP LAN Broadcast at: {_resolved_hub_host}")
        return f"http://{_resolved_hub_host}:{HUB_PORT}"
  except Exception:
    log.debug(f"UDP LAN discovery timed out; falling back to HUB_IP '{HUB_IP}'")

  _resolved_hub_host = HUB_IP
  _discovery_method = "Static IP"
  return f"http://{_resolved_hub_host}:{HUB_PORT}"

# Load persistent Level 1 Edge icons cache
CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "icons_cache.json")
icon_cache = {}
try:
  if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
      raw = json.load(f)
    if isinstance(raw, dict):
      for k, val in raw.items():
        if isinstance(val, list):
          icon_cache[k] = {"bitmap": val, "updated_at": 0.0, "permanent": k in ("clear", "green")}
        elif isinstance(val, dict):
          icon_cache[k] = val
    log.info(f"Loaded {len(icon_cache)} icons from Level 1 Edge cache.")
except Exception as e:
  log.warning(f"Could not load Level 1 Edge icons cache: {e}")

now_ts = time.time()
if "clear" not in icon_cache:
  icon_cache["clear"] = {"bitmap": [[0]*12 for _ in range(8)], "updated_at": now_ts, "permanent": True}
if "green" not in icon_cache:
  icon_cache["green"] = {"bitmap": [[1]*12 for _ in range(8)], "updated_at": now_ts, "permanent": True}

_generating_labels = set()
_cache_lock = threading.Lock()

def _save_cache():
  try:
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
      json.dump(icon_cache, f, indent=2)
  except Exception as e:
    log.warning(f"Failed to save Level 1 Edge icons cache: {e}")

def _get_bitmap_entry(label: str):
  entry = icon_cache.get(label)
  if not entry:
    return None
  if not entry.get("permanent", False):
    age = time.time() - entry.get("updated_at", 0.0)
    if age > TTL_SECONDS or entry.get("updated_at", 0.0) == 0.0:
      log.info(f"Level 1 Edge cache expired for '{label}' (age: {age:.1f}s > {TTL_SECONDS}s)")
      return None
  return entry.get("bitmap")

def _generate_icon_thread(label: str):
  log.info(f"Querying Qonclave Hub (Level 2 Cache) for icon silhouette: '{label}'...")
  try:
    url = f"{get_hub_base_url()}/edge/icon"
    resp = requests.get(url, params={"label": label}, timeout=15)
    if resp.status_code == 200 and resp.json().get("ok"):
      grid = resp.json().get("bitmap")
      if grid and len(grid) == 8 and len(grid[0]) == 12:
        with _cache_lock:
          icon_cache[label] = {
            "bitmap": grid,
            "updated_at": time.time(),
            "permanent": False
          }
          _save_cache()
        log.info(f"Successfully received and cached Level 1 icon from Hub for '{label}'")
        try:
          bitstring = "".join("1" if val else "0" for r in grid for val in r[:12])
          Bridge.call("set_custom_led_array", bitstring)
          ui.send_message("sync_icons", message=icon_cache)
          ui.send_message("led_status", message={"state": "active", "trigger": label, "bitmap": grid, "ai_generated": True})
        except Exception as e:
          log.warning(f"Failed to push updated icon to UI: {e}")
  except Exception as e:
    log.error(f"Failed to query Hub for icon '{label}': {e}")
  finally:
    with _cache_lock:
      _generating_labels.discard(label)

def get_or_trigger_icon(label: str):
  if not label:
    return _get_bitmap_entry("clear") or [[0]*12 for _ in range(8)], False
  label = label.lower().strip()
  with _cache_lock:
    bmp = _get_bitmap_entry(label)
    if bmp:
      return bmp, False
    if label not in _generating_labels:
      _generating_labels.add(label)
      threading.Thread(target=_generate_icon_thread, args=(label,), daemon=True).start()
  return _get_bitmap_entry("clear") or [[0]*12 for _ in range(8)], True

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

# A 360-degree dual-lens rig stacks rear-camera on top, front-camera on the
# bottom of a single frame; the LED matrix's row mapping needs to be
# vertically flipped to match (front/bottom-half -> top rows, rear/top-half
# -> bottom rows). Plain USB/IP cameras must NOT set this.
CAMERA_DUAL_LENS_STACKED = os.environ.get("CAMERA_DUAL_LENS_STACKED", "false").strip().lower() in ("1", "true", "yes")
CAMERA_HORIZONTAL_FOV_DEGREES = float(os.environ.get("CAMERA_HORIZONTAL_FOV_DEGREES", "70"))
CAMERA_DUAL_LENS_FOV_DEGREES = float(os.environ.get("CAMERA_DUAL_LENS_FOV_DEGREES", "180"))

if CAMERA_SOURCE == "ip":
  IP_CAMERA_URL = os.environ.get("IP_CAMERA_URL", "http://192.168.18.65:8080/video")
  IP_CAMERA_USERNAME = os.environ.get("IP_CAMERA_USERNAME") or None
  IP_CAMERA_PASSWORD = os.environ.get("IP_CAMERA_PASSWORD") or None
  IP_CAMERA_FPS = int(os.environ.get("IP_CAMERA_FPS", "10"))
  camera = IPCamera(url=IP_CAMERA_URL, username=IP_CAMERA_USERNAME,
                    password=IP_CAMERA_PASSWORD, fps=IP_CAMERA_FPS)
elif CAMERA_SOURCE == "file":
  VIDEO_FILE_PATH = os.environ.get("VIDEO_FILE_PATH", "/app/media/walking_front_view.mp4")
  if not VIDEO_FILE_PATH:
    raise RuntimeError("CAMERA_SOURCE=file requires VIDEO_FILE_PATH to be set")
  VIDEO_FILE_LOOP = os.environ.get("VIDEO_FILE_LOOP", "true").strip().lower() not in ("0", "false", "no")
  VIDEO_FILE_FPS = int(os.environ.get("VIDEO_FILE_FPS", "25"))
  camera = FileCamera(VIDEO_FILE_PATH, loop=VIDEO_FILE_LOOP, fps=VIDEO_FILE_FPS)
else:
  # VIDEO_DEVICE is set by arduino-app-cli when it detects a local USB
  # camera; USB_CAMERA_DEVICE lets that be overridden explicitly.
  USB_CAMERA_DEVICE = os.environ.get("USB_CAMERA_DEVICE") or os.environ.get("VIDEO_DEVICE", 0)
  camera = V4LCamera(device=USB_CAMERA_DEVICE)

ui = WebUI()

WEB_UI_USERNAME = os.environ.get("WEB_UI_USERNAME", "").strip()
WEB_UI_PASSWORD = os.environ.get("WEB_UI_PASSWORD", "").strip()
if WEB_UI_USERNAME and WEB_UI_PASSWORD:
  ui.app.add_middleware(BasicAuthMiddleware, username=WEB_UI_USERNAME, password=WEB_UI_PASSWORD)
  log.info("Web UI protected with HTTP Basic Auth.")
else:
  log.warning("WEB_UI_USERNAME/WEB_UI_PASSWORD not set: Web UI is running WITHOUT authentication.")

# --- Live preview with track-ID overlay ------------------------------------
# The preview runs at camera rate, not detection rate: every frame the
# detection brick pulls from the camera is also handed to a publisher thread
# (via the capture() tee below), which draws the last-known track boxes on it
# and publishes it for /track-preview to stream as MJPEG. Detection (~1.5 Hz
# on this board) only refreshes which boxes get drawn -- video smoothness no
# longer waits on inference, matching how the stock EI runner UI behaves.
# /camera-preview is the page the frontend iframe actually loads (an <img>
# pointed at /track-preview) -- kept as its own indirection so what the
# preview shows can change without an index.html edit.
_latest_track_preview: bytes | None = None
# Bumped on every publish so a streaming client can tell "new frame" from "same
# frame again" without comparing bytes, and woken the moment a frame lands
# rather than on a fixed tick -- a timed poll both delayed each new frame by up
# to its interval and re-sent unchanged ones in between.
_track_preview_seq = 0
_track_preview_cond = threading.Condition()
# A gap much longer than a frame interval means the camera stalled. Re-send the
# last frame then: it keeps the MJPEG connection alive, and gives the generator
# a yield point at which a client that has gone away can be noticed instead of
# blocking its worker thread forever.
_PREVIEW_KEEPALIVE_SEC = 2.0
# Cap on published preview fps; frames above the cap are skipped before any
# encode work happens. Bounds the encode cost (~10ms/frame on this board) and
# per-client bandwidth while staying far above the ~1.5 fps detection rate.
PREVIEW_MAX_FPS = float(os.environ.get("PREVIEW_MAX_FPS", "15"))

# Last-known overlay state, written by send_detections_to_ui, read by the
# publisher thread. Boxes older than the max age are dropped rather than pinned
# onto live video -- e.g. when the person leaves or the runner stalls.
_overlay_state_lock = threading.Lock()
_overlay_tracks: list = []
_overlay_labels: dict = {}
# Track id of the current follow target (None when there isn't a visible
# one); its preview box is drawn in the highlight color.
_overlay_target_id = None
_overlay_updated_at = 0.0
_OVERLAY_MAX_AGE_SEC = 3.0

# Latest raw camera frame, written by the capture() tee on the brick's camera
# loop thread. Latest-wins: the publisher skips frames it can't keep up with.
_camera_frame = None
_camera_frame_seq = 0
_camera_frame_cond = threading.Condition()

# Cap on frames handed to the detection brick (and so to the EI runner). The
# runner decodes, re-encodes, and writes to disk every frame it receives but
# infers at only ~1.5/s on this board, so frames beyond that burn a large slice
# of gst + node CPU on work that is thrown away. 3 fps stays comfortably above
# the inference rate (measured: gating to 3 does not lower detections/sec)
# while cutting the wasted decode work. 0 disables the gate.
RUNNER_MAX_FPS = float(os.environ.get("RUNNER_MAX_FPS", "3"))
_next_runner_frame_at = 0.0

_original_capture = camera.capture

def _capture_tee(*args, **kwargs):
  """camera.capture wrapper: publish every frame to the preview thread, but
  pass only RUNNER_MAX_FPS of them through to the detection brick. Returning
  None takes the brick's normal no-frame path (brief sleep, poll again). The
  brick stays the camera's only reader; runs on its camera-loop thread only,
  so the gate state needs no lock."""
  global _camera_frame, _camera_frame_seq, _next_runner_frame_at
  frame = _original_capture(*args, **kwargs)
  if frame is None:
    return None
  with _camera_frame_cond:
    _camera_frame = frame
    _camera_frame_seq += 1
    _camera_frame_cond.notify_all()
  if RUNNER_MAX_FPS > 0:
    now = time.monotonic()
    if now < _next_runner_frame_at:
      return None
    _next_runner_frame_at = now + 1.0 / RUNNER_MAX_FPS
  return frame

camera.capture = _capture_tee

def _preview_publisher():
  global _latest_track_preview, _track_preview_seq
  last_seq = 0
  min_interval = 1.0 / PREVIEW_MAX_FPS if PREVIEW_MAX_FPS > 0 else 0.0
  next_publish_at = 0.0
  while True:
    with _camera_frame_cond:
      _camera_frame_cond.wait_for(lambda: _camera_frame_seq != last_seq)
      frame = _camera_frame
      last_seq = _camera_frame_seq
    now = time.monotonic()
    if now < next_publish_at:
      continue
    next_publish_at = now + min_interval
    with _overlay_state_lock:
      tracks = _overlay_tracks
      labels = _overlay_labels
      target_id = _overlay_target_id
      overlay_fresh = (now - _overlay_updated_at) <= _OVERLAY_MAX_AGE_SEC
    if tracks and overlay_fresh:
      # Copy: the brick's camera loop may still be JPEG-encoding this same
      # array for the runner, and drawing mutates it.
      jpeg = draw_track_overlay_bgr(frame.copy(), tracks, labels,
                                    highlight_track_id=target_id)
    else:
      jpeg = encode_jpeg(frame)
    if jpeg is None:
      continue
    with _track_preview_cond:
      _latest_track_preview = jpeg
      _track_preview_seq += 1
      _track_preview_cond.notify_all()

threading.Thread(target=_preview_publisher, name="PreviewPublisher", daemon=True).start()

def _camera_preview_page(_request):
  return HTMLResponse(
    '<!doctype html><html><head><style>'
    'html,body{margin:0;width:100%;height:100%;background:#111;overflow:hidden}'
    'img{width:100%;height:100%;object-fit:contain}'
    '</style></head><body><img src="/track-preview" alt="Camera preview"></body></html>'
  )

def _track_preview_mjpeg():
  """Stream each published preview frame once, as soon as it is published.

  Each client tracks the sequence number it last sent, so several viewers can
  stream independently and none of them re-sends a frame another one consumed.
  """
  last_seq = -1
  while True:
    with _track_preview_cond:
      _track_preview_cond.wait_for(
        lambda: _track_preview_seq != last_seq, timeout=_PREVIEW_KEEPALIVE_SEC)
      frame = _latest_track_preview
      last_seq = _track_preview_seq
    if frame:
      yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"

def _track_preview_stream(_request):
  return StreamingResponse(_track_preview_mjpeg(), media_type="multipart/x-mixed-replace; boundary=frame")

ui.app.add_route("/camera-preview", _camera_preview_page, methods=["GET"])
ui.app.add_route("/track-preview", _track_preview_stream, methods=["GET"])

def _send_current_hub_status():
  try:
    ui.send_message("hub_status", message={
      "online": _hub_online,
      "host": _resolved_hub_host or HUB_IP,
      "port": HUB_PORT,
      "method": _discovery_method
    })
  except Exception:
    pass

ui.on_message("request_icons", lambda sid, data: (ui.send_message("sync_icons", message=icon_cache), _send_current_hub_status()))

# Assigned further down (needs the follow-priority env block); predefined so
# the health-monitor thread below can already reference it safely.
priority_client = None

def _monitor_hub_health():
  global _hub_online, _resolved_hub_host, _discovery_method
  while True:
    was_online = _hub_online
    try:
      url = f"{get_hub_base_url()}/health"
      resp = requests.get(url, timeout=3)
      _hub_online = (resp.status_code == 200)
    except Exception:
      _hub_online = False
      _resolved_hub_host = None
      _discovery_method = "Searching..." if HUB_DISCOVERY_ENABLED else "Static IP (Discovery Disabled)"
    if _hub_online and not was_online and priority_client is not None:
      # Hub just came (back) online: refresh the known-person priority map
      # now instead of waiting out the refresh interval. Off-thread so a
      # slow/failed fetch can't delay the next health check.
      threading.Thread(target=priority_client.refresh_now,
                       name="PriorityMapRefresh", daemon=True).start()
    _send_current_hub_status()
    time.sleep(5.0)

threading.Thread(target=_monitor_hub_health, name="HubHealthMonitor", daemon=True).start()
detection_stream = VideoObjectDetection(camera, confidence=0.5, debounce_sec=0.0, camera_preview=True)

ui.on_message("override_th", lambda sid, threshold: detection_stream.override_threshold(threshold))

ROBOT_DIRECTIONS = {"LEFT", "RIGHT", "FORWARD", "BACKWARD"}

def _execute_robot_command(message):
  if not isinstance(message, dict):
    raise ValueError("Robot command must be an object")

  direction = str(message.get("direction", "")).strip().upper()
  if direction == "STOP":
    Bridge.call("stop_robot")
    person_centering.note_motion()
    person_distance.note_motion()
    return {"ok": True, "direction": "STOP"}

  if direction not in ROBOT_DIRECTIONS:
    raise ValueError(f"Unsupported direction: {direction or '(empty)'}")

  magnitude = int(message.get("magnitude", 1))
  if not 1 <= magnitude <= 360:
    raise ValueError("Magnitude must be between 1 and 360")

  started = Bridge.call("move_robot", direction, magnitude)
  if started is False:
    raise RuntimeError("Orientation sensor is not ready")
  # Blank BOTH followers for this move's estimated duration plus the pipeline
  # latency window, whatever the source (auto-centering, distance keeping,
  # web UI, hub MQTT): motion stales bearings and box sizes alike. Small
  # turns finish between detection callbacks, so waiting to *observe*
  # robot_motion_active would miss exactly the moves that cause ping-pong.
  if direction in {"LEFT", "RIGHT"}:
    est_duration = magnitude * person_centering.estimated_ms_per_degree / 1000.0
  else:
    est_duration = float(magnitude)  # FORWARD/BACKWARD magnitude is seconds
  person_centering.note_motion(est_duration)
  person_distance.note_motion(est_duration)
  return {
    "ok": True,
    "direction": direction,
    "magnitude": magnitude,
    "unit": "degrees" if direction in {"LEFT", "RIGHT"} else "seconds",
  }

def handle_robot_move(sid, message):
  try:
    status = _execute_robot_command(message)
    ui.send_message("robot_move_status", message=status)
  except (TypeError, ValueError) as e:
    log.warning(f"Rejected robot command from UI client {sid}: {e}")
    ui.send_message("robot_move_status", message={"ok": False, "error": str(e)})
  except Exception as e:
    log.error(f"Robot command failed: {e}")
    ui.send_message("robot_move_status", message={"ok": False, "error": "MCU command failed"})

ui.on_message("robot_move", handle_robot_move)

def _execute_buzzer_command(message):
  if not isinstance(message, dict):
    raise ValueError("Buzzer command must be an object")

  action = str(message.get("action", "")).strip().lower()
  frequency = int(message.get("frequency", 440))
  duration = int(message.get("duration", 0))

  if action in ("believer", "song"):
    Bridge.call("play_believer")
    return {"ok": True, "action": "believer", "song": "Believer - Imagine Dragons"}
  elif action in ("start", "tone"):
    if "frequency" in message:
      Bridge.call("trigger_buzzer", frequency, duration)
    else:
      Bridge.call("play_believer")
    return {"ok": True, "action": action, "frequency": frequency, "duration": duration}
  elif action in ("stop", "notone"):
    Bridge.call("stop_buzzer")
    return {"ok": True, "action": action}
  else:
    raise ValueError(f"Unsupported buzzer action: {action or '(empty)'}")

def handle_buzzer(sid, message):
  try:
    status = _execute_buzzer_command(message)
    ui.send_message("buzzer_status", message=status)
  except (TypeError, ValueError) as e:
    log.warning(f"Rejected buzzer command from UI client {sid}: {e}")
    ui.send_message("buzzer_status", message={"ok": False, "error": str(e)})
  except Exception as e:
    log.error(f"Buzzer command failed: {e}")
    ui.send_message("buzzer_status", message={"ok": False, "error": "MCU buzzer command failed"})

ui.on_message("buzzer", handle_buzzer)

# 1. Listen for Potentiometer Knob adjustments from MCU (sketch.ino)
def handle_knob_change(percentage_str):
  try:
    val = float(percentage_str) / 100.0
    detection_stream.override_threshold(val)
    ui.send_message("knob_update", message={"threshold": val})
  except Exception:
    pass

Bridge.provide("on_knob_change", handle_knob_change)
green_bmp = _get_bitmap_entry("green")
if green_bmp:
  try:
    Bridge.call("set_custom_led_array", "".join("1" if val else "0" for r in green_bmp for val in r[:12]))
  except Exception as e:
    # The MCU bridge can still be coming online after a container restart.
    # LED initialization is cosmetic and must not take down video analysis.
    log.warning(f"Initial LED update unavailable; continuing without it: {e}")

# --- Hub event forwarding: notify the Qonclave hub when a person is detected ---

# Periodic person-detected escalation to POST /edge/event. Each of those
# escalations runs the hub's VLM, so this is effectively "send images for the
# hub's model every N seconds while a person is visible".
#
# PERIODIC REASONING IS OFF. The VLM now runs only event-driven: a posture
# investigation, the dashboard button, or a CAPTURE SMS. Interval-driven
# frames competed with those for the same single-threaded VLM, so a real
# collapse could queue behind a routine "person is visible" frame -- and the
# routine frame carried no posture context to reason about anyway.
#
# HUB_PERIODIC_REASONING_ENABLED is the authoritative switch and defaults to
# off. It overrides the interval knobs below, so a stale .env on the board
# cannot silently re-enable the periodic path; set it to 1 (and give
# HUB_EVENT_INTERVAL_SEC a positive value) to bring it back.
HUB_PERIODIC_REASONING_ENABLED = os.environ.get(
  "HUB_PERIODIC_REASONING_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
HUB_EVENT_ENABLED = os.environ.get("HUB_EVENT_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
PERSON_CONFIDENCE_THRESHOLD = float(os.environ.get("PERSON_CONFIDENCE_THRESHOLD", "0.7"))
HUB_EVENT_HYSTERESIS_SEC = float(os.environ.get("HUB_EVENT_HYSTERESIS_SEC", "10"))
_raw_interval = os.environ.get("HUB_EVENT_INTERVAL_SEC", "").strip()
if _raw_interval:
  HUB_EVENT_INTERVAL_SEC = max(0.0, float(_raw_interval))
else:
  HUB_EVENT_INTERVAL_SEC = HUB_EVENT_HYSTERESIS_SEC if HUB_EVENT_ENABLED else 0.0
if not HUB_PERIODIC_REASONING_ENABLED:
  HUB_EVENT_INTERVAL_SEC = 0.0
HUB_EVENT_TIMEOUT_SEC = float(os.environ.get("HUB_EVENT_TIMEOUT_SEC", "5"))
ESCALATION_DIR = os.environ.get("ESCALATION_DIR", "/app/escalations")
ESCALATION_MAX_FILES = int(os.environ.get("ESCALATION_MAX_FILES", "100"))

# --- Person tracking: assign a persistent ID + coarse direction to each
# detected person across frames, using only the bounding boxes VideoObjectDetection
# already emits. See python/person_tracker.py for the matching approach.
person_tracker = PersonTracker(
  max_disappeared=int(os.environ.get("PERSON_TRACK_MAX_DISAPPEARED", "10")),
  max_distance=float(os.environ.get("PERSON_TRACK_MAX_DISTANCE_PX", "150")),
  direction_history=int(os.environ.get("PERSON_TRACK_DIRECTION_HISTORY", "5")),
  min_movement_px=float(os.environ.get("PERSON_TRACK_MIN_MOVEMENT_PX", "10")),
)
person_centering = PersonCenteringController(
  enabled=os.environ.get("PERSON_AUTO_CENTER_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on"),
  tolerance_degrees=float(os.environ.get("PERSON_CENTER_TOLERANCE_DEGREES", "3")),
  max_turn_degrees=float(os.environ.get("PERSON_CENTER_MAX_TURN_DEGREES", "90")),
  minimum_interval_seconds=float(os.environ.get("PERSON_CENTER_MIN_INTERVAL_SEC", "0.75")),
  estimated_ms_per_degree=float(os.environ.get("PERSON_CENTER_ESTIMATED_MS_PER_DEGREE", "12")),
  settle_seconds=float(os.environ.get("PERSON_CENTER_SETTLE_SEC", "0.35")),
  # Bearings arriving within this window after any robot motion ends come from
  # frames captured while the camera was still moving (detection pipeline
  # latency is ~1s on this board) and are discarded rather than acted on.
  post_motion_blank_seconds=float(os.environ.get("PERSON_CENTER_POST_MOTION_BLANK_SEC", "1.5")),
  turn_gain=float(os.environ.get("PERSON_CENTER_TURN_GAIN", "0.7")),
)

# --- Distance keeping: centering only ROTATES toward the followed person;
# this drives FORWARD when they are too small in frame to see clearly and
# BACKWARD when they are uncomfortably close, holding inside the deadband.
# Size comes from the larger box dimension, so a FALLEN person (wide, short
# box) is not misread as "far away" and driven into. One 1s nudge per paced,
# confirmed decision -- see python/person_distance.py for the safety order.
person_distance = PersonDistanceController(
  enabled=os.environ.get("FOLLOW_DISTANCE_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on"),
  approach_below=float(os.environ.get("FOLLOW_DISTANCE_APPROACH_BELOW", "0.35")),
  retreat_above=float(os.environ.get("FOLLOW_DISTANCE_RETREAT_ABOVE", "0.65")),
  step_seconds=int(os.environ.get("FOLLOW_DISTANCE_STEP_SEC", "1")),
  minimum_interval_seconds=float(os.environ.get("FOLLOW_DISTANCE_MIN_INTERVAL_SEC", "2.5")),
  confirm_frames=int(os.environ.get("FOLLOW_DISTANCE_CONFIRM_FRAMES", "3")),
  post_motion_blank_seconds=float(os.environ.get("FOLLOW_DISTANCE_POST_MOTION_BLANK_SEC", "1.5")),
)

# --- Investigation approach: when the hub opens an investigation (posture
# SUSPICIOUS/DANGER) it asks this device for one frame. Before capturing, the
# robot turns to face the target and drives forward briefly so the VLM gets a
# close-up instead of a distant smudge. See python/investigation_approach.py.
#
# The whole approach must finish inside the hub's capture timeout
# (QONCLAVE_INVESTIGATION_CAPTURE_TIMEOUT_SEC, 10s) or the hub gives up and
# uses a buffered crop -- discarding the very frame this exists to produce.
# The budget is deliberately well under it to leave room for the upload.
INVESTIGATION_APPROACH_ENABLED = os.environ.get(
  "INVESTIGATION_APPROACH_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")
INVESTIGATION_APPROACH_FORWARD_SEC = int(
  os.environ.get("INVESTIGATION_APPROACH_FORWARD_SEC", "1"))
INVESTIGATION_APPROACH_TOLERANCE_DEGREES = float(
  os.environ.get("INVESTIGATION_APPROACH_TOLERANCE_DEGREES", "8"))
INVESTIGATION_APPROACH_MAX_TURN_DEGREES = float(
  os.environ.get("INVESTIGATION_APPROACH_MAX_TURN_DEGREES", "45"))
INVESTIGATION_APPROACH_SETTLE_SEC = float(
  os.environ.get("INVESTIGATION_APPROACH_SETTLE_SEC", "0.6"))
INVESTIGATION_APPROACH_BUDGET_SEC = float(
  os.environ.get("INVESTIGATION_APPROACH_BUDGET_SEC", "6"))
# A bearing older than this is not trusted to aim a turn. Detection runs at
# ~1.5 Hz, so anything past a couple of frames may describe where the person
# WAS; the approach then skips the turn rather than turning the wrong way.
INVESTIGATION_APPROACH_BEARING_MAX_AGE_SEC = float(
  os.environ.get("INVESTIGATION_APPROACH_BEARING_MAX_AGE_SEC", "3"))

# --- Known-person priority following: prefer recognized people (lowest hub
# priority number wins), hold a missing known target for a grace period
# instead of chasing an unknown, and only then fall back to the
# longest-established unknown track. See python/follow_target_selector.py.
FOLLOW_KNOWN_GRACE_FRAMES = int(os.environ.get("FOLLOW_KNOWN_GRACE_FRAMES", "10"))
FOLLOW_PRIORITY_REFRESH_SEC = float(os.environ.get("FOLLOW_PRIORITY_REFRESH_SEC", "15"))
FOLLOW_PRIORITY_TIMEOUT_SEC = float(os.environ.get("FOLLOW_PRIORITY_TIMEOUT_SEC", "3"))

# --- Per-track analysis: sample each tracked person's crop to the hub's
# POST /track/analyze endpoint (not the motor-following pipeline above).
# One crop per request, fanned out hub-side to the requested analyzers:
# face resolves who each track_id is (until known, then never again), pose
# runs continuously at ~4 Hz for fall detection.
TRACK_RECOGNITION_ENABLED = os.environ.get("TRACK_RECOGNITION_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")
TRACK_ANALYZERS = tuple(
  a.strip() for a in os.environ.get("TRACK_ANALYZERS", "face,pose").split(",") if a.strip()
)
TRACK_CROP_PADDING = float(os.environ.get("TRACK_CROP_PADDING", "0.25"))
TRACK_CROP_PADDING_TOP = float(os.environ.get("TRACK_CROP_PADDING_TOP", "0.8"))
TRACK_CROP_MIN_SIZE_PX = int(os.environ.get("TRACK_CROP_MIN_SIZE_PX", "40"))
TRACK_CROP_MIN_VISIBLE_RATIO = float(os.environ.get("TRACK_CROP_MIN_VISIBLE_RATIO", "0.85"))
TRACK_CROP_POSE_MIN_BOX_HEIGHT_PX = int(os.environ.get("TRACK_CROP_POSE_MIN_BOX_HEIGHT_PX", "100"))
TRACK_CROP_POSE_MIN_VISIBLE_RATIO = float(os.environ.get("TRACK_CROP_POSE_MIN_VISIBLE_RATIO", "0.5"))
TRACK_CROPS_DIR = os.environ.get("TRACK_CROPS_DIR", "/app/track_crops")
FACE_SAMPLE_INTERVAL_SEC = float(os.environ.get("FACE_SAMPLE_INTERVAL_SEC", "0.5"))
POSE_SAMPLE_INTERVAL_SEC = float(os.environ.get("POSE_SAMPLE_INTERVAL_SEC", "0.25"))
ANALYSIS_REQUEST_TIMEOUT_SEC = float(os.environ.get("ANALYSIS_REQUEST_TIMEOUT_SEC", "5"))

identity_map = IdentityMap()
follow_selector = FollowTargetSelector(grace_frames=FOLLOW_KNOWN_GRACE_FRAMES)
priority_client = PriorityMapClient(
  get_hub_base_url=get_hub_base_url,
  refresh_sec=FOLLOW_PRIORITY_REFRESH_SEC,
  timeout_sec=FOLLOW_PRIORITY_TIMEOUT_SEC,
  logger=log,
)
priority_client.start()
analysis_client = AnalysisClient(
  get_hub_base_url=get_hub_base_url,
  timeout_sec=ANALYSIS_REQUEST_TIMEOUT_SEC,
  face_interval_sec=FACE_SAMPLE_INTERVAL_SEC,
  pose_interval_sec=POSE_SAMPLE_INTERVAL_SEC,
  analyzers=TRACK_ANALYZERS,
  logger=log,
  device_id=DEVICE_ID,
)

# Track_ids from the *current* frame, so a /track/analyze response that
# arrives after its track was dropped (hub was slow, person already left) is
# ignored instead of resurrecting a stale identity.
_live_track_ids: set = set()
_live_track_ids_lock = threading.Lock()
_last_logged_identity_snapshot: dict = {}

# Latest pose sub-result per live track, for the Web UI's pose_status
# message. The full keypoint time series lives on the hub (track_store);
# the edge only mirrors the newest status/score.
_latest_pose: dict = {}
_latest_pose_lock = threading.Lock()


def _on_analysis_result(track_id: int, result: dict, latency_s: float):
  with _live_track_ids_lock:
    is_live = track_id in _live_track_ids
  face = result.get("face")
  # A positive face match is safe to retain for its own numeric track ID even
  # when crop/HTTP work completed after the detector's grace window. Without
  # this exception, the hub can prove Track 1 is Alice while the edge discards
  # that result and continues sending weaker face samples for Track 1.
  known_face = bool(face and face.get("status") == "known")
  if not is_live and not identity_map.is_recent(track_id) and not known_face:
    log.debug(f"Ignoring /track/analyze result for track {track_id}: outside inactive grace period ({latency_s * 1000:.0f}ms round trip)")
    return
  if face:
    identity_map.merge(track_id, face)
  pose = result.get("pose")
  if pose:
    with _latest_pose_lock:
      _latest_pose[track_id] = {
        "status": pose.get("status"),
        "mean_score": pose.get("mean_score"),
      }


def _crop_and_analyze(frame: bytes, due_map: dict):
  # ONE background thread per detection callback: decode the frame once,
  # crop every due track from it, and post each crop. This used to be one
  # thread (and one cv2.imdecode) per track per sample -- at pose's 4 Hz
  # cadence across N tracks that decode cost is real on the UNO Q.
  # analysis_client.claim() already reserved every track in due_map before
  # this thread was started, so it's safe to take as long as it needs here.
  crops = crop_persons(
    frame, {tid: box for tid, (box, _due, _identity) in due_map.items()},
    padding=TRACK_CROP_PADDING,
    padding_top=TRACK_CROP_PADDING_TOP,
    face_min_size_px=TRACK_CROP_MIN_SIZE_PX,
    face_min_visible_ratio=TRACK_CROP_MIN_VISIBLE_RATIO,
    pose_min_box_height_px=TRACK_CROP_POSE_MIN_BOX_HEIGHT_PX,
    pose_min_visible_ratio=TRACK_CROP_POSE_MIN_VISIBLE_RATIO,
  )
  for track_id, (_box, due, known_identity) in due_map.items():
    entry = crops.get(track_id)
    # Send only the analyzers that are both due AND accept this crop's
    # geometry (e.g. a small-but-visible person samples face, not pose).
    analyzers = due & entry["analyzers_ok"] if entry else set()
    if not analyzers:
      log.debug(f"Rejected crop for track {track_id}: too small or badly clipped for all due analyzers")
      analysis_client.release(track_id)
      continue
    save_crop_locally(track_id, entry["jpeg"], TRACK_CROPS_DIR)
    analysis_client.send_claimed(
      track_id, entry["jpeg"], analyzers, _on_analysis_result,
      person_box=entry["person_box"],
      known_identity=known_identity,
    )


def _log_identity_map_if_changed(snapshot: dict):
  global _last_logged_identity_snapshot
  if snapshot == _last_logged_identity_snapshot:
    return
  _last_logged_identity_snapshot = dict(snapshot)
  for track_id, entry in sorted(snapshot.items()):
    label = entry["name"] if entry["status"] == "known" else entry["status"].replace("_", " ").capitalize()
    log.info(f"Track {track_id} — {label}")


_last_follow_signature = None


def _follow_desc(sig) -> str:
  _state, track_id, identity, priority, _missing = sig
  if track_id is None:
    return "no target"
  if identity:
    return f"{identity} track {track_id} (known priority {priority})"
  return f"unknown track {track_id}"


def _log_follow_state_if_changed(selection: dict):
  """Log follow transitions only, never unchanged per-frame state (model:
  _log_identity_map_if_changed). Grace ticks change missing_frames, so each
  one logs its own 'Holding ... n/N frames' line, as the spec asks."""
  global _last_follow_signature
  sig = (selection["state"], selection["track_id"], selection["identity"],
         selection["priority"], selection["missing_frames"])
  if sig == _last_follow_signature:
    return
  prev = _last_follow_signature
  _last_follow_signature = sig

  state = selection["state"]
  if state == "known_target_missing":
    log.info(
      f"Holding known target {selection['identity']}: "
      f"missing {selection['missing_frames']}/{selection['grace_frames']} frames"
    )
  elif state == "fallback_unknown" and selection["reason"] == "grace_expired_fallback":
    log.info(f"Known-target grace expired: falling back to unknown track {selection['track_id']}")
  else:
    log.info(
      f"Follow target changed: {_follow_desc(prev) if prev else 'none'} -> "
      f"{_follow_desc(sig)} [{selection['reason']}]"
    )


_hub_event_lock = threading.Lock()
_last_hub_event_at = 0.0

# Newest bearing to the follow target, published by the detection callback and
# read by the investigation-capture thread (which has no frame of its own to
# measure from). (track_id, angle_degrees, monotonic timestamp) or None.
_follow_bearing_lock = threading.Lock()
_follow_bearing: tuple[int, float, float] | None = None


def _note_follow_bearing(track_id: int, angle_degrees: float):
  global _follow_bearing
  with _follow_bearing_lock:
    _follow_bearing = (track_id, angle_degrees, time.monotonic())


def _recent_follow_bearing(max_age_seconds: float) -> tuple[int, float] | None:
  """(track_id, bearing) if fresh enough to aim a turn at, else None."""
  with _follow_bearing_lock:
    snapshot = _follow_bearing
  if snapshot is None:
    return None
  track_id, angle, measured_at = snapshot
  if time.monotonic() - measured_at > max_age_seconds:
    return None
  return track_id, angle


def _prune_escalation_frames():
  # Filenames are ISO-8601 timestamps (colons swapped for dashes), so a
  # plain lexicographic sort is also a chronological sort.
  try:
    names = sorted(f[:-4] for f in os.listdir(ESCALATION_DIR) if f.endswith(".jpg"))
  except OSError:
    return

  excess = len(names) - ESCALATION_MAX_FILES
  if excess <= 0:
    return

  for name in names[:excess]:
    for ext in (".jpg", ".json"):
      try:
        os.remove(os.path.join(ESCALATION_DIR, f"{name}{ext}"))
      except OSError:
        pass


def _save_escalation_frame(confidence: float, frame: bytes, timestamp: str):
  base_path = os.path.join(ESCALATION_DIR, timestamp.replace(":", "-"))
  try:
    os.makedirs(ESCALATION_DIR, exist_ok=True)
    with open(f"{base_path}.jpg", "wb") as f:
      f.write(frame)
    with open(f"{base_path}.json", "w") as f:
      json.dump({
        "timestamp": timestamp,
        "threshold": PERSON_CONFIDENCE_THRESHOLD,
        "confidence": confidence,
      }, f)
    _prune_escalation_frames()
  except OSError as e:
    log.error(f"Failed to save escalation frame locally: {e}")


def _post_person_event(confidence: float, frame: bytes):
  url = f"{get_hub_base_url()}/edge/event"
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
  # HUB_EVENT_INTERVAL_SEC is forced to 0 unless periodic reasoning is
  # explicitly enabled, so this is the disabled path by default.
  if HUB_EVENT_INTERVAL_SEC <= 0 or not frame:
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
    if now - _last_hub_event_at < HUB_EVENT_INTERVAL_SEC:
      return
    _last_hub_event_at = now

  timestamp = datetime.now(UTC).isoformat()
  threading.Thread(target=_save_escalation_frame, args=(best_confidence, frame, timestamp), daemon=True).start()
  threading.Thread(target=_post_person_event, args=(best_confidence, frame), daemon=True).start()


# Register a callback for when all objects are detected
def send_detections_to_ui(detections: dict, frame: bytes | None = None):
  person_tracks = person_tracker.update(detections.get("person", []))

  active_track_ids = {t["track_id"] for t in person_tracks}
  with _live_track_ids_lock:
    _live_track_ids.clear()
    _live_track_ids.update(active_track_ids)

  for dropped_id in identity_map.prune(active_track_ids):
    analysis_client.forget(dropped_id)
    remove_crop_locally(dropped_id, TRACK_CROPS_DIR)
    with _latest_pose_lock:
      _latest_pose.pop(dropped_id, None)

  if TRACK_RECOGNITION_ENABLED and frame:
    due_map = {}
    for track in person_tracks:
      track_id = track["track_id"]
      identity = identity_map.get(track_id)
      due = analysis_client.analyzers_due(track_id, identity["status"] == "known")
      if due:
        known_identity = identity["name"] if identity["status"] == "known" else None
        due_map[track_id] = (track["bounding_box_xyxy"], due, known_identity)
    if due_map:
      # Claim synchronously (cheap) so a frame arriving before the crop/encode
      # work below finishes can't also decide to sample these tracks; the
      # actual decode/crop/encode/save/POSTs all happen off this
      # detection-callback thread, on one background thread for the whole
      # frame (decode once, crop all due tracks).
      for track_id, (_box, due, _known_identity) in due_map.items():
        analysis_client.claim(track_id, due)
      threading.Thread(
        target=_crop_and_analyze, args=(frame, due_map),
        name="TrackAnalyze", daemon=True,
      ).start()

  identity_snapshot = {tid: identity_map.get(tid) for tid in active_track_ids}
  if person_tracks:
    _log_identity_map_if_changed(identity_snapshot)
    ui.send_message("identity_map", message=identity_snapshot)
    with _latest_pose_lock:
      pose_snapshot = {tid: _latest_pose[tid] for tid in active_track_ids if tid in _latest_pose}
    if pose_snapshot:
      ui.send_message("pose_status", message=pose_snapshot)

  # Pick this frame's follow target. Runs unconditionally -- an empty frame
  # still ticks the known-target grace counter. Motor commands below only
  # ever come from selection["track"], which is a track from THIS frame's
  # person_tracks or None (during grace / no target), so a stale bounding box
  # structurally cannot produce a turn.
  selection = follow_selector.select(
    person_tracks, identity_snapshot, priority_client.snapshot())
  target_track = selection["track"]
  _log_follow_state_if_changed(selection)
  ui.send_message("follow_status",
                  message={k: v for k, v in selection.items() if k != "track"})

  # Refresh the overlay the camera-rate preview publisher draws; the frames
  # themselves no longer come from this callback (see _preview_publisher).
  global _overlay_tracks, _overlay_labels, _overlay_target_id, _overlay_updated_at
  if person_tracks:
    labels = {
      tid: f"Track {tid}: {entry['name']}" if entry["status"] == "known"
           else f"Track {tid}: {entry['status'].replace('_', ' ').capitalize()}"
      for tid, entry in identity_snapshot.items()
    }
    if target_track is not None:
      tid = selection["track_id"]
      suffix = (f" [FOLLOWING, P{selection['priority']}]"
                if selection["priority"] is not None else " [FOLLOWING]")
      labels[tid] = labels.get(tid, f"Track {tid}") + suffix
  else:
    labels = {}
  with _overlay_state_lock:
    _overlay_tracks = person_tracks
    _overlay_labels = labels
    _overlay_target_id = target_track["track_id"] if target_track is not None else None
    _overlay_updated_at = time.monotonic()

  if target_track is not None:
    cx, cy = target_track["centroid"]
    frame_w, frame_h = camera.resolution
    angle_to_center = horizontal_bearing_degrees(
      (cx, cy), frame_w, frame_h,
      horizontal_fov_degrees=CAMERA_HORIZONTAL_FOV_DEGREES,
      dual_lens_stacked=CAMERA_DUAL_LENS_STACKED,
      dual_lens_fov_degrees=CAMERA_DUAL_LENS_FOV_DEGREES,
    )
    target_track["angle_to_center_degrees"] = angle_to_center
    # Publish for the investigation-capture thread, which needs to know which
    # way to turn before its close-up but never sees a frame itself.
    _note_follow_bearing(target_track["track_id"], angle_to_center)
    x1, y1, x2, y2 = target_track["bounding_box_xyxy"]
    # A stacked dual-lens frame is two views one above the other, so a person
    # occupies at most half its pixel height; measure against one view's
    # height or every ratio reads as "far away" and the robot keeps advancing.
    size_frame_h = frame_h / 2 if CAMERA_DUAL_LENS_STACKED else frame_h
    size_ratio = size_ratio_of(x2 - x1, y2 - y1, frame_w, size_frame_h)
    log.debug(
      f"Tracking person {target_track['track_id']}: angle_to_center={angle_to_center:.2f}°, "
      f"size_ratio={size_ratio}, motion={target_track['direction']}"
    )
    ui.send_message("person_tracking_status", message={
      "track_id": target_track["track_id"],
      "angle_to_center_degrees": angle_to_center,
      "centered": abs(angle_to_center) <= person_centering.tolerance_degrees,
      "centroid": [cx, cy],
      "size_ratio": size_ratio,
      "distance_zone": person_distance.zone_for(size_ratio),
    })

    try:
      motion_active = Bridge.call("robot_motion_active")
      if motion_active:
        # Backup for moves note_motion() didn't see start (e.g. a long manual
        # move still running): keep extending the blank window while the MCU
        # reports motion, so bearings from these frames are also discarded.
        person_centering.note_motion()
        person_distance.note_motion()
        turn = None
      else:
        turn = person_centering.command_for(angle_to_center, target_track["track_id"])
      if turn:
        status = _execute_robot_command({
          "direction": turn.direction,
          "magnitude": turn.magnitude,
        })
        status.update({
          "automatic": True,
          "track_id": turn.track_id,
          "angle_error_degrees": turn.angle_error_degrees,
        })
        ui.send_message("robot_move_status", message=status)
        log.info(
          f"Auto-centering person {turn.track_id}: {turn.direction} "
          f"{turn.magnitude}° (measured error {turn.angle_error_degrees:.2f}°)"
        )
      elif (not motion_active
            and abs(angle_to_center) <= person_centering.tolerance_degrees):
        # Distance keeping runs only once the person is CENTERED and no turn
        # was issued this frame: turning has priority (driving forward while
        # misaligned closes distance toward the wrong point, and on a stacked
        # dual-lens rig the person may even be BEHIND the robot), and the
        # centered gate also means FORWARD always moves toward the person the
        # bearing was measured on.
        move = person_distance.command_for(
          x2 - x1, y2 - y1, frame_w, size_frame_h,
          target_track["track_id"])
        if move:
          status = _execute_robot_command({
            "direction": move.direction,
            "magnitude": move.magnitude,
          })
          status.update({
            "automatic": True,
            "track_id": move.track_id,
            "size_ratio": move.size_ratio,
            "reason": move.reason,
          })
          ui.send_message("robot_move_status", message=status)
          log.info(
            f"Distance-keeping person {move.track_id}: {move.direction} "
            f"{move.magnitude}s ({move.reason}, size_ratio={move.size_ratio})"
          )
    except Exception as e:
      log.error(f"Person follow command failed: {e}")

  if person_tracks:
    # A person is visible: show a position on the LED matrix instead of the
    # usual object icon. Prefer the follow target; with no current target
    # (e.g. mid-grace) fall back to the most-established track for DISPLAY
    # only -- it feeds no bearings and no motor commands.
    display_track = (target_track if target_track is not None
                     else max(person_tracks, key=lambda t: t["frames_tracked"]))
    cx, cy = display_track["centroid"]
    frame_w, frame_h = camera.resolution
    if CAMERA_DUAL_LENS_STACKED:
      cy = frame_h - cy  # rear (top half) -> bottom rows, front (bottom half) -> top rows
    bitmap = person_display_bitmap((cx, cy), frame_w, frame_h)
    bitstring = "".join("1" if val else "0" for r in bitmap for val in r[:12])
    Bridge.call("set_custom_led_array", bitstring)
    ui.send_message("led_status", message={"state": "active", "trigger": "person", "bitmap": bitmap, "ai_generated": False})
  elif detections:
    first_obj = list(detections.keys())[0]
    bitmap, is_generating = get_or_trigger_icon(first_obj)
    bitstring = "".join("1" if val else "0" for r in bitmap for val in r[:12]) if bitmap else "0" * 96
    Bridge.call("set_custom_led_array", bitstring)
    ui.send_message("led_status", message={"state": "active", "trigger": first_obj, "bitmap": bitmap, "ai_generated": (first_obj not in ["clear", "green"])})
  else:
    clear_bmp = _get_bitmap_entry("clear") or [[0]*12 for _ in range(8)]
    bitstring = "".join("1" if val else "0" for r in clear_bmp for val in r[:12])
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

# --- Hub->edge command channel: connect to the MQTT broker and listen for
# commands the hub pushes to this device.

def _wait_for_motion_idle(deadline: float) -> bool:
  """Block until the MCU reports motion finished, or the deadline passes.

  Returns True if motion actually ended in time. The MCU runs turns
  closed-loop on the IMU with its own deadline, so a commanded duration is an
  estimate, not a promise -- polling is what makes the following capture
  reliably sharp instead of hopefully sharp."""
  while time.monotonic() < deadline:
    try:
      if not Bridge.call("robot_motion_active"):
        return True
    except Exception as e:
      # No motion feedback available: fall back to the commanded timing
      # rather than spinning here until the deadline.
      log.warning(f"robot_motion_active unavailable while approaching: {e}")
      return False
    time.sleep(0.05)
  return False


def _approach_target_before_capture(event_id: str, requested: bool):
  """Turn to face the tracked person and drive forward briefly, so the
  investigation frame is a close-up rather than a distant smudge.

  ``requested`` is the hub's ``approach`` flag: true for posture-triggered
  events, false for operator-requested checks (which want the scene as it is
  and have no flagged person to approach).

  Every failure here is non-fatal: the capture still happens from wherever
  the robot ended up. A worse photo is recoverable, a skipped investigation
  is not."""
  if not INVESTIGATION_APPROACH_ENABLED or not requested:
    return

  recent = _recent_follow_bearing(INVESTIGATION_APPROACH_BEARING_MAX_AGE_SEC)
  bearing = recent[1] if recent else None
  steps = plan_approach(
    bearing,
    forward_seconds=INVESTIGATION_APPROACH_FORWARD_SEC,
    tolerance_degrees=INVESTIGATION_APPROACH_TOLERANCE_DEGREES,
    max_turn_degrees=INVESTIGATION_APPROACH_MAX_TURN_DEGREES,
    ms_per_degree=person_centering.estimated_ms_per_degree,
    settle_seconds=INVESTIGATION_APPROACH_SETTLE_SEC,
    budget_seconds=INVESTIGATION_APPROACH_BUDGET_SEC,
  )
  log.info(
    f"Investigation {event_id}: approaching "
    f"{'track ' + str(recent[0]) if recent else 'target (no recent bearing)'} "
    f"-> {describe_approach(steps)}"
  )

  budget_ends = time.monotonic() + INVESTIGATION_APPROACH_BUDGET_SEC
  for step in steps:
    try:
      _execute_robot_command({
        "direction": step.direction,
        "magnitude": step.magnitude,
      })
    except Exception as e:
      log.error(f"Investigation {event_id}: approach step "
                f"{step.direction} {step.magnitude} failed: {e}")
      break
    # Each step's own estimate, never past the overall budget: a turn whose
    # closed-loop correction runs long must not eat the forward step's time.
    step_deadline = min(budget_ends, time.monotonic() + step.estimated_seconds
                        + INVESTIGATION_APPROACH_SETTLE_SEC)
    if not _wait_for_motion_idle(step_deadline):
      # Either no feedback or the step overran; sleep out whatever budget
      # this step was owed so the capture isn't taken mid-move.
      time.sleep(max(0.0, min(step_deadline, budget_ends) - time.monotonic()))

  if steps:
    # Guarantee the wheels are stopped (a step may have overrun its estimate),
    # THEN let the chassis stop rocking and the camera pipeline emit a frame
    # from the new position -- only after that is _camera_frame worth reading.
    try:
      Bridge.call("stop_robot")
    except Exception as e:
      log.warning(f"Investigation {event_id}: stop_robot after approach "
                  f"failed: {e}")
    time.sleep(INVESTIGATION_APPROACH_SETTLE_SEC)


def _capture_investigation_image(command: dict):
  """Answer a capture_investigation_image command: approach the person, then
  grab the freshest raw camera frame (full resolution, not a person crop) and
  POST it back to the hub's /edge/investigation with the event_id. Failures
  are logged only -- the hub falls back to its buffered evidence frames after
  its capture timeout."""
  event_id = str(command.get("event_id") or "")
  # Absent flag defaults to approaching: the command's normal source is a
  # posture event, and an older hub sends no flag at all.
  _approach_target_before_capture(event_id, bool(command.get("approach", True)))
  with _camera_frame_cond:
    frame = _camera_frame
  jpeg = encode_jpeg(frame) if frame is not None else None
  if jpeg is None:
    log.warning(f"Investigation {event_id}: no camera frame available to capture")
    return
  try:
    url = f"{get_hub_base_url()}/edge/investigation"
    resp = requests.post(
      url,
      data={
        "event_id": event_id,
        "device_id": DEVICE_ID,
        "status": "capture_complete",
        "track_id": str(command.get("track_id") or ""),
      },
      files={"image": (f"{event_id}.jpg", jpeg, "image/jpeg")},
      timeout=HUB_EVENT_TIMEOUT_SEC,
    )
    log.info(f"Investigation {event_id}: capture sent -> {resp.status_code} {resp.text[:200]}")
  except requests.RequestException as e:
    log.error(f"Investigation {event_id}: failed to send capture to {url}: {e}")


def _handle_hub_command(command: dict):
  log.info(f"Received hub command: {command}")
  if not isinstance(command, dict):
    log.warning(f"Ignoring invalid hub command format: {command}")
    return

  # The hub sends the same value under both "type" and "command"; accept either
  # so an older hub build still reaches the right branch.
  cmd_type = str(command.get("type") or command.get("command") or "").strip().lower()
  action = str(command.get("action", "")).strip().lower()

  if cmd_type == "capture_investigation_image":
    # Off-thread: the MQTT callback must not block on the approach (which
    # drives the robot for ~1s) plus camera + HTTP work.
    threading.Thread(
      target=_capture_investigation_image, args=(command,),
      name="InvestigationCapture", daemon=True,
    ).start()
    return

  if cmd_type == "robot_move":
    try:
      status = _execute_robot_command(command)
      log.info(f"Executed hub robot command: {status}")
      ui.send_message("robot_move_status", message=status)
    except (TypeError, ValueError) as e:
      log.warning(f"Rejected hub robot command: {e}")
    except Exception as e:
      log.error(f"Hub robot command failed: {e}")
  elif cmd_type == "buzzer" or action in ("start", "stop", "tone", "notone", "believer", "song"):
    try:
      status = _execute_buzzer_command(command)
      log.info(f"Executed hub buzzer command: {status}")
      ui.send_message("buzzer_status", message=status)
    except (TypeError, ValueError) as e:
      log.warning(f"Rejected hub buzzer command: {e}")
    except Exception as e:
      log.error(f"Hub buzzer command failed: {e}")
  else:
    log.warning(f"Ignoring unsupported hub command: {command}")

if HUB_EVENT_INTERVAL_SEC > 0:
  log.info(f"Periodic hub reasoning ENABLED: one frame per "
           f"{HUB_EVENT_INTERVAL_SEC}s while a person is visible")
else:
  log.info("Periodic hub reasoning disabled -- the VLM runs event-driven only "
           "(posture investigation, dashboard button, CAPTURE SMS)")
log.info(
  f"Investigation approach: "
  + (f"forward {INVESTIGATION_APPROACH_FORWARD_SEC}s after facing the target "
     f"(within {INVESTIGATION_APPROACH_MAX_TURN_DEGREES:.0f} deg), "
     f"{INVESTIGATION_APPROACH_BUDGET_SEC}s budget"
     if INVESTIGATION_APPROACH_ENABLED else "disabled (capture in place)")
)
log.info(
  "Distance keeping: "
  + (f"hold person at size {person_distance.approach_below:.2f}-"
     f"{person_distance.retreat_above:.2f} of frame, "
     f"{person_distance.step_seconds}s nudges every "
     f">={person_distance.minimum_interval_seconds}s after "
     f"{person_distance.confirm_frames} confirming frames"
     if person_distance.enabled else "disabled (rotate only)")
)

mqtt_client = EdgeMQTTClient(
  device_id=DEVICE_ID,
  host=MQTT_HOST,
  port=MQTT_PORT,
  enabled=MQTT_ENABLED,
  on_command=_handle_hub_command,
  logger=log,
)
mqtt_client.start()

App.run()
