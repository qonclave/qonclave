"""
state.py — shared config, event store, VLM instance, and request helpers for
the Qonclave hub. Imported by both the edge and user route blueprints so they
share one upload dir, one event ring buffer, and one VLM backend.
"""

from __future__ import annotations

import collections
import datetime as _dt
import json
import logging
import os
import sys
import threading
import uuid

from flask import request

# Make "import vlm_backend" work regardless of CWD.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vlm_backend import VLMBackend, DEFAULT_PROMPT  # noqa: E402,F401 (re-exported)

log = logging.getLogger("qonclave.hub")

# --- config -----------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
UPLOAD_DIR = os.path.join(HERE, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

HOST = os.environ.get("QONCLAVE_HOST", "0.0.0.0")
PORT = int(os.environ.get("QONCLAVE_PORT", "8000"))
MAX_UPLOAD_MB = int(os.environ.get("QONCLAVE_MAX_UPLOAD_MB", "16"))
ALLOWED_EXT = {"jpg", "jpeg", "png", "bmp", "webp"}
SCHEMA_VERSION = "0.1"

# --- shared VLM backend (cheap to construct; does NOT import geniex) --------
vlm = VLMBackend()

# --- event store (in-memory ring buffer for the dashboard) ------------------
EVENTS_MAX = int(os.environ.get("QONCLAVE_EVENTS_MAX", "50"))
_events: "collections.deque[dict]" = collections.deque(maxlen=EVENTS_MAX)
_events_lock = threading.Lock()
_latest_frame: dict = {"name": None}  # basename of most recent stored frame


def record_event(event: dict, frame_name: str | None):
    with _events_lock:
        _events.appendleft(event)
        if frame_name:
            _latest_frame["name"] = frame_name


def recent_events(limit: int | None = None) -> tuple[list[dict], str | None]:
    with _events_lock:
        items = list(_events)[: (limit or EVENTS_MAX)]
        return items, _latest_frame["name"]


def latest_frame_name() -> str | None:
    with _events_lock:
        return _latest_frame["name"]


# --- request helpers --------------------------------------------------------
def timestamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _ext_from_content_type(ct: str) -> str:
    ct = (ct or "").lower()
    if "png" in ct:
        return "png"
    if "webp" in ct:
        return "webp"
    if "bmp" in ct:
        return "bmp"
    return "jpg"  # default incl. image/jpeg


def save_incoming_image() -> tuple[str | None, str | None]:
    """
    Extract an image from the request and save it to UPLOAD_DIR.
    Supports two shapes:
      1. multipart/form-data with a file field named "image" (browsers, curl -F)
      2. raw image bytes as the request body with Content-Type image/*
         (dead simple for Arduino UNO Q)
    Returns (saved_path, error_message).
    """
    # Shape 1: multipart file field
    if "image" in request.files:
        f = request.files["image"]
        if not f or f.filename == "":
            return None, "empty 'image' file field"
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else "jpg"
        if ext not in ALLOWED_EXT:
            ext = "jpg"
        name = f"{timestamp()}-{uuid.uuid4().hex[:8]}.{ext}"
        path = os.path.join(UPLOAD_DIR, name)
        f.save(path)
        return path, None

    # Shape 2: raw body (Arduino-friendly)
    ct = request.headers.get("Content-Type", "")
    if request.data and (ct.startswith("image/") or "octet-stream" in ct):
        ext = _ext_from_content_type(ct)
        name = f"{timestamp()}-{uuid.uuid4().hex[:8]}.{ext}"
        path = os.path.join(UPLOAD_DIR, name)
        with open(path, "wb") as fh:
            fh.write(request.data)
        return path, None

    return None, (
        "no image found. Send multipart form field 'image', or POST raw image "
        "bytes with Content-Type image/jpeg."
    )


def parse_edge_event() -> dict:
    """
    Extract the edge event metadata that accompanies a frame. Tolerant of how a
    constrained device sends it:
      * multipart field "event" containing a JSON string
      * individual multipart form fields (device_id, event_id, edge_confidence…)
      * query-string params (?device_id=…&edge_confidence=…) for raw-body POSTs
      * request JSON body (when no file part)
    Missing fields are simply absent; nothing here raises.
    """
    ev: dict = {}
    fields = ("device_id", "event_id", "event_type", "edge_model",
              "edge_confidence", "threshold", "frame_id", "created_at")

    # a) a JSON blob in form field "event"
    raw = request.form.get("event")
    if raw:
        try:
            ev.update(json.loads(raw))
        except (ValueError, TypeError):
            log.warning("event field was not valid JSON; ignoring")

    # b) individual form fields
    for k in fields:
        if k in request.form and k not in ev:
            ev[k] = request.form.get(k)

    # c) query params (handy for raw-body Arduino POSTs)
    for k in fields:
        if k in request.args and k not in ev:
            ev[k] = request.args.get(k)

    # d) a full JSON body (no multipart file)
    if not ev and request.is_json:
        body = request.get_json(silent=True) or {}
        if isinstance(body, dict):
            ev.update(body)

    # normalize numeric fields when present
    for num in ("edge_confidence", "threshold"):
        if num in ev and ev[num] is not None:
            try:
                ev[num] = float(ev[num])
            except (ValueError, TypeError):
                pass
    return ev


def request_prompt() -> str:
    return (request.form.get("prompt") or request.args.get("prompt")
            or request.headers.get("X-Prompt") or DEFAULT_PROMPT)


def verdict_from_verify(v: dict) -> tuple[bool, float | None, str]:
    """
    Map the VLM's structured verify() result to the (hub_verified,
    hub_confidence, alert) triple the edge/dashboard contract expects.

    verify() already did the classification with json_mode, so this is a plain
    field read — no keyword matching on prose. On machines where the VLM is
    unavailable (non-Snapdragon), person_present is None -> not verified.
    A dedicated person-detector gate (YOLOv8) can supplement this later so
    verification works even without the VLM.
    """
    if not v.get("available"):
        return False, None, "unverified (reasoning unavailable on this hub)"
    person = v.get("person_present")
    conf = v.get("confidence")
    alert = v.get("alert") or ("Person verified near camera" if person
                               else "No person confirmed in frame")
    return bool(person), conf, alert
