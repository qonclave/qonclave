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
from identity_map import IdentityMap
from led_display import person_display_bitmap
from mqtt_client import EdgeMQTTClient
from person_centering import PersonCenteringController, horizontal_bearing_degrees
from person_tracker import PersonTracker
from recognition_client import RecognitionClient
from track_crop import crop_person, remove_crop_locally, save_crop_locally
from track_overlay import draw_track_overlay

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
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
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

WEB_UI_USERNAME = os.environ.get("WEB_UI_USERNAME", "").strip()
WEB_UI_PASSWORD = os.environ.get("WEB_UI_PASSWORD", "").strip()
if WEB_UI_USERNAME and WEB_UI_PASSWORD:
  ui.app.add_middleware(BasicAuthMiddleware, username=WEB_UI_USERNAME, password=WEB_UI_PASSWORD)
  log.info("Web UI protected with HTTP Basic Auth.")
else:
  log.warning("WEB_UI_USERNAME/WEB_UI_PASSWORD not set: Web UI is running WITHOUT authentication.")

# --- Live preview with track-ID overlay: send_detections_to_ui redraws each
# tracked person's box + "Track N: <name/status>" label onto the current
# frame (track_overlay.draw_track_overlay) and caches it here; /track-preview
# streams that cache as MJPEG. /camera-preview is the page the frontend
# iframe actually loads (an <img> pointed at /track-preview) -- kept as its
# own indirection so what the preview shows can change without an index.html
# edit.
_latest_track_preview: bytes | None = None
_track_preview_lock = threading.Lock()

def _camera_preview_page(_request):
  return HTMLResponse(
    '<!doctype html><html><head><style>'
    'html,body{margin:0;width:100%;height:100%;background:#111;overflow:hidden}'
    'img{width:100%;height:100%;object-fit:contain}'
    '</style></head><body><img src="/track-preview" alt="Camera preview"></body></html>'
  )

def _track_preview_mjpeg():
  while True:
    with _track_preview_lock:
      frame = _latest_track_preview
    if frame:
      yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
    time.sleep(0.1)

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

def _monitor_hub_health():
  global _hub_online, _resolved_hub_host, _discovery_method
  while True:
    try:
      url = f"{get_hub_base_url()}/health"
      resp = requests.get(url, timeout=3)
      _hub_online = (resp.status_code == 200)
    except Exception:
      _hub_online = False
      _resolved_hub_host = None
      _discovery_method = "Searching..." if HUB_DISCOVERY_ENABLED else "Static IP (Discovery Disabled)"
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
    return {"ok": True, "direction": "STOP"}

  if direction not in ROBOT_DIRECTIONS:
    raise ValueError(f"Unsupported direction: {direction or '(empty)'}")

  magnitude = int(message.get("magnitude", 1))
  if not 1 <= magnitude <= 360:
    raise ValueError("Magnitude must be between 1 and 360")

  started = Bridge.call("move_robot", direction, magnitude)
  if started is False:
    raise RuntimeError("Orientation sensor is not ready")
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
  Bridge.call("set_custom_led_array", "".join("1" if val else "0" for r in green_bmp for val in r[:12]))

# --- Hub event forwarding: notify the Qonclave hub when a person is detected ---

PERSON_CONFIDENCE_THRESHOLD = float(os.environ.get("PERSON_CONFIDENCE_THRESHOLD", "0.7"))
HUB_EVENT_HYSTERESIS_SEC = float(os.environ.get("HUB_EVENT_HYSTERESIS_SEC", "10"))
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
)

# --- Per-track face recognition: sample each tracked person's crop to the
# hub's POST /recognize endpoint (not the motor-following pipeline above --
# this only resolves who each track_id is).
TRACK_RECOGNITION_ENABLED = os.environ.get("TRACK_RECOGNITION_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")
TRACK_CROP_PADDING = float(os.environ.get("TRACK_CROP_PADDING", "0.25"))
TRACK_CROP_PADDING_TOP = float(os.environ.get("TRACK_CROP_PADDING_TOP", "0.8"))
TRACK_CROP_MIN_SIZE_PX = int(os.environ.get("TRACK_CROP_MIN_SIZE_PX", "40"))
TRACK_CROP_MIN_VISIBLE_RATIO = float(os.environ.get("TRACK_CROP_MIN_VISIBLE_RATIO", "0.85"))
TRACK_CROPS_DIR = os.environ.get("TRACK_CROPS_DIR", "/app/track_crops")
RECOGNITION_SAMPLE_INTERVAL_SEC = float(os.environ.get("RECOGNITION_SAMPLE_INTERVAL_SEC", "1.0"))
RECOGNITION_REQUEST_TIMEOUT_SEC = float(os.environ.get("RECOGNITION_REQUEST_TIMEOUT_SEC", "5"))

identity_map = IdentityMap()
recognition_client = RecognitionClient(
  get_hub_base_url=get_hub_base_url,
  timeout_sec=RECOGNITION_REQUEST_TIMEOUT_SEC,
  sample_interval_sec=RECOGNITION_SAMPLE_INTERVAL_SEC,
  logger=log,
)

# Track_ids from the *current* frame, so a /recognize response that arrives
# after its track was dropped (hub was slow, person already left) is ignored
# instead of resurrecting a stale identity.
_live_track_ids: set = set()
_live_track_ids_lock = threading.Lock()
_last_logged_identity_snapshot: dict = {}


def _on_recognition_result(track_id: int, result: dict, latency_s: float):
  with _live_track_ids_lock:
    is_live = track_id in _live_track_ids
  if not is_live:
    log.debug(f"Ignoring /recognize result for track {track_id}: no longer active ({latency_s * 1000:.0f}ms round trip)")
    return
  identity_map.merge(track_id, result)


def _crop_and_recognize(track_id: int, frame: bytes, bounding_box_xyxy):
  # Runs entirely off the detection-callback thread: the decode/crop/encode
  # work here used to run inline in send_detections_to_ui, adding real CPU
  # cost to the hot video-processing path on every unresolved track, every
  # frame. recognition_client.claim() already reserved this track before
  # this thread was started, so it's safe to take as long as it needs here.
  crop = crop_person(
    frame, bounding_box_xyxy,
    padding=TRACK_CROP_PADDING,
    padding_top=TRACK_CROP_PADDING_TOP,
    min_size_px=TRACK_CROP_MIN_SIZE_PX,
    min_visible_ratio=TRACK_CROP_MIN_VISIBLE_RATIO,
  )
  if crop is None:
    log.debug(f"Rejected crop for track {track_id}: too small or badly clipped")
    recognition_client.release(track_id)
    return
  save_crop_locally(track_id, crop, TRACK_CROPS_DIR)
  recognition_client.send_claimed(track_id, crop, _on_recognition_result)


def _log_identity_map_if_changed(snapshot: dict):
  global _last_logged_identity_snapshot
  if snapshot == _last_logged_identity_snapshot:
    return
  _last_logged_identity_snapshot = dict(snapshot)
  for track_id, entry in sorted(snapshot.items()):
    label = entry["name"] if entry["status"] == "known" else entry["status"].replace("_", " ").capitalize()
    log.info(f"Track {track_id} — {label}")


_hub_event_lock = threading.Lock()
_last_hub_event_at = 0.0


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
    recognition_client.forget(dropped_id)
    remove_crop_locally(dropped_id, TRACK_CROPS_DIR)

  if TRACK_RECOGNITION_ENABLED and frame:
    for track in person_tracks:
      track_id = track["track_id"]
      if not recognition_client.should_sample(track_id, identity_map.is_known(track_id)):
        continue
      # Claim synchronously (cheap) so a frame arriving before the crop/encode
      # work below finishes can't also decide to sample this track; the actual
      # decode/crop/encode/save/POST all happen off this detection-callback
      # thread, on their own background thread.
      recognition_client.claim(track_id)
      threading.Thread(
        target=_crop_and_recognize, args=(track_id, frame, track["bounding_box_xyxy"]),
        name=f"Recognize-{track_id}", daemon=True,
      ).start()

  if person_tracks:
    identity_snapshot = {tid: identity_map.get(tid) for tid in active_track_ids}
    _log_identity_map_if_changed(identity_snapshot)
    ui.send_message("identity_map", message=identity_snapshot)

  if frame:
    global _latest_track_preview
    if person_tracks:
      labels = {
        tid: f"Track {tid}: {entry['name']}" if entry["status"] == "known"
             else f"Track {tid}: {entry['status'].replace('_', ' ').capitalize()}"
        for tid, entry in identity_snapshot.items()
      }
      preview_frame = draw_track_overlay(frame, person_tracks, labels)
    else:
      preview_frame = frame
    with _track_preview_lock:
      _latest_track_preview = preview_frame

  if person_tracks:
    # A person is actively tracked: show its position on the LED matrix
    # instead of the usual object icon. Pick the most-established track so a
    # briefly-flickering new detection doesn't steal the display.
    tracked = max(person_tracks, key=lambda t: t["frames_tracked"])
    cx, cy = tracked["centroid"]
    frame_w, frame_h = camera.resolution
    angle_to_center = horizontal_bearing_degrees(
      (cx, cy), frame_w, frame_h,
      horizontal_fov_degrees=CAMERA_HORIZONTAL_FOV_DEGREES,
      dual_lens_stacked=CAMERA_DUAL_LENS_STACKED,
      dual_lens_fov_degrees=CAMERA_DUAL_LENS_FOV_DEGREES,
    )
    tracked["angle_to_center_degrees"] = angle_to_center
    log.debug(
      f"Tracking person {tracked['track_id']}: angle_to_center={angle_to_center:.2f}°, "
      f"motion={tracked['direction']}"
    )
    ui.send_message("person_tracking_status", message={
      "track_id": tracked["track_id"],
      "angle_to_center_degrees": angle_to_center,
      "centered": abs(angle_to_center) <= person_centering.tolerance_degrees,
      "centroid": [cx, cy],
    })

    try:
      motion_active = Bridge.call("robot_motion_active")
      turn = None if motion_active else person_centering.command_for(
        angle_to_center, tracked["track_id"]
      )
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
    except Exception as e:
      log.error(f"Person auto-centering command failed: {e}")

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
def _handle_hub_command(command: dict):
  log.info(f"Received hub command: {command}")
  if not isinstance(command, dict):
    log.warning(f"Ignoring invalid hub command format: {command}")
    return

  cmd_type = str(command.get("type", "")).strip().lower()
  action = str(command.get("action", "")).strip().lower()

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
