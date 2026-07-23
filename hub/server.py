"""
server.py — Qonclave hub HTTP server.

Endpoints:
    GET  /health          -> liveness + VLM availability
    POST /reason          -> raw VLM tester: image in, reasoning text out
    POST /event           -> EDGE endpoint: edge event JSON + frame in,
                             schema-compliant verification response out
    GET  /events          -> DASHBOARD data: recent events + results (JSON)
    GET  /frames/<name>   -> serve a stored frame
    GET  /latest.jpg      -> serve the most recent frame
    GET  /dashboard       -> DASHBOARD page (live event/verification view)
    GET  /                -> test upload webpage
    GET  /static/...      -> static assets

Design goals:
    * Runs on ANY laptop (regular x86 Windows/Linux included). The reasoning
      part is conditional — see hub/vlm_backend.py — so only that piece is
      Snapdragon-only. Everything else (upload, logging, webpage) is testable
      anywhere.
    * Arduino UNO Q friendly: /reason and /event accept BOTH a normal
      multipart form upload (field name "image") AND a raw image body
      (Content-Type image/*), which is the simplest thing to POST from a
      constrained device.
    * Everything is logged to the terminal where the server runs.

Run:
    pip install -r hub/requirements.txt
    python hub/server.py                 # http://0.0.0.0:8000
    # options via env: QONCLAVE_HOST, QONCLAVE_PORT, QONCLAVE_WARMUP=1
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

from flask import (
    Flask, jsonify, request, send_from_directory,
)

# Make "import vlm_backend" work regardless of CWD.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vlm_backend import VLMBackend, DEFAULT_PROMPT  # noqa: E402

# --- logging: everything to the terminal -----------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
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

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# Single shared backend. Construction is cheap and does NOT import geniex.
vlm = VLMBackend()

# --- event store (in-memory ring buffer for the dashboard) -----------------
EVENTS_MAX = int(os.environ.get("QONCLAVE_EVENTS_MAX", "50"))
_events: "collections.deque[dict]" = collections.deque(maxlen=EVENTS_MAX)
_events_lock = threading.Lock()
_latest_frame: dict = {"name": None}  # basename of most recent stored frame

SCHEMA_VERSION = "0.1"


def _record_event(event: dict, frame_name: str | None):
    with _events_lock:
        _events.appendleft(event)
        if frame_name:
            _latest_frame["name"] = frame_name


# --- helpers ----------------------------------------------------------------
def _ext_from_content_type(ct: str) -> str:
    ct = (ct or "").lower()
    if "png" in ct:
        return "png"
    if "webp" in ct:
        return "webp"
    if "bmp" in ct:
        return "bmp"
    return "jpg"  # default incl. image/jpeg


def _timestamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _save_incoming_image() -> tuple[str | None, str | None]:
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
        name = f"{_timestamp()}-{uuid.uuid4().hex[:8]}.{ext}"
        path = os.path.join(UPLOAD_DIR, name)
        f.save(path)
        return path, None

    # Shape 2: raw body (Arduino-friendly)
    ct = request.headers.get("Content-Type", "")
    if request.data and (ct.startswith("image/") or "octet-stream" in ct):
        ext = _ext_from_content_type(ct)
        name = f"{_timestamp()}-{uuid.uuid4().hex[:8]}.{ext}"
        path = os.path.join(UPLOAD_DIR, name)
        with open(path, "wb") as fh:
            fh.write(request.data)
        return path, None

    return None, (
        "no image found. Send multipart form field 'image', or POST raw image "
        "bytes with Content-Type image/jpeg."
    )


def _parse_edge_event() -> dict:
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

    # a) a JSON blob in form field "event"
    raw = request.form.get("event")
    if raw:
        try:
            ev.update(json.loads(raw))
        except (ValueError, TypeError):
            log.warning("event field was not valid JSON; ignoring")

    # b) individual form fields
    for k in ("device_id", "event_id", "event_type", "edge_model",
              "edge_confidence", "threshold", "frame_id", "created_at"):
        if k in request.form and k not in ev:
            ev[k] = request.form.get(k)

    # c) query params (handy for raw-body Arduino POSTs)
    for k in ("device_id", "event_id", "event_type", "edge_model",
              "edge_confidence", "threshold", "frame_id", "created_at"):
        if k in request.args and k not in ev:
            ev[k] = request.args.get(k)

    # d) a full JSON body (no multipart file)
    if not ev and request.is_json:
        try:
            body = request.get_json(silent=True) or {}
            if isinstance(body, dict):
                ev.update(body)
        except Exception:
            pass

    # normalize numeric fields when present
    for num in ("edge_confidence", "threshold"):
        if num in ev and ev[num] is not None:
            try:
                ev[num] = float(ev[num])
            except (ValueError, TypeError):
                pass
    return ev


def _verify_from_reasoning(result: dict) -> tuple[bool, float | None, str]:
    """
    Turn a VLM reasoning result into the (hub_verified, hub_confidence, alert)
    triple the edge/dashboard contract expects.

    Base MVP heuristic: if reasoning ran and its text mentions a person, treat
    the event as verified. On machines where the VLM is unavailable
    (non-Snapdragon), we cannot verify from reasoning, so hub_verified=false.
    A dedicated person-detector gate (YOLOv8) can replace this later so
    verification works even without the VLM.
    """
    if not result.get("available") or not result.get("text"):
        return False, None, "unverified (reasoning unavailable on this hub)"
    text = result["text"].lower()
    person = any(w in text for w in ("person", "people", "human", "man", "woman", "someone"))
    if person:
        return True, result.get("hub_confidence"), "Person verified near camera"
    return False, result.get("hub_confidence"), "No person confirmed in frame"


# --- endpoints --------------------------------------------------------------
@app.get("/health")
def health():
    log.info("GET /health from %s", request.remote_addr)
    return jsonify({
        "status": "ok",
        "service": "qonclave-hub",
        "time": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "vlm": vlm.status(),
    })


@app.post("/reason")
def reason():
    client = request.remote_addr
    # Prompt can come from form field, query string, or header; else default.
    prompt = (
        request.form.get("prompt")
        or request.args.get("prompt")
        or request.headers.get("X-Prompt")
        or DEFAULT_PROMPT
    )
    log.info("POST /reason from %s (content-type=%s, len=%s)",
             client, request.headers.get("Content-Type"),
             request.content_length)

    path, err = _save_incoming_image()
    if err:
        log.warning("POST /reason rejected from %s: %s", client, err)
        return jsonify({"ok": False, "error": err}), 400

    log.info("Saved upload -> %s ; running reasoning...", path)
    result = vlm.reason(path, prompt=prompt)

    if result.get("error") and not result.get("available"):
        log.warning("Reasoning unavailable: %s", result["error"])
    elif result.get("error"):
        log.error("Reasoning error: %s", result["error"])
    else:
        preview = (result.get("text") or "")[:200].replace("\n", " ")
        log.info("Reasoning result (%.3ss): %s",
                 result.get("latency_s"), preview)

    return jsonify({
        "ok": result.get("error") is None,
        "image_saved_as": os.path.basename(path),
        **result,
    })


# --- EDGE endpoint ----------------------------------------------------------
@app.post("/event")
def event():
    """
    The real edge contract (Arduino UNO Q -> hub). Accepts the escalation frame
    plus the edge event metadata, runs hub verification (VLM reasoning), records
    the result for the dashboard, and returns the schema-compliant response
    described in qonclave_plan.md §5.3.
    """
    client = request.remote_addr
    log.info("POST /event from %s (content-type=%s, len=%s)",
             client, request.headers.get("Content-Type"), request.content_length)

    edge = _parse_edge_event()
    prompt = (request.form.get("prompt") or request.args.get("prompt")
              or request.headers.get("X-Prompt") or DEFAULT_PROMPT)

    path, err = _save_incoming_image()
    if err:
        log.warning("POST /event rejected from %s: %s", client, err)
        return jsonify({"received": False, "error": err}), 400

    event_id = edge.get("event_id") or f"{_timestamp()}-{uuid.uuid4().hex[:8]}"
    frame_name = os.path.basename(path)
    log.info("Edge event %s | device=%s | edge_conf=%s | frame=%s",
             event_id, edge.get("device_id"), edge.get("edge_confidence"), frame_name)

    result = vlm.reason(path, prompt=prompt)
    hub_verified, hub_conf, alert = _verify_from_reasoning(result)

    # schema-compliant response (plan §5.3)
    response = {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "received": True,
        "hub_verified": hub_verified,
        "hub_confidence": hub_conf,
        "identity_status": "not_enabled",   # stretch: known/unknown face
        "alert": alert,
    }

    if hub_verified:
        log.info("ALERT [%s]: %s", event_id, alert)
    else:
        log.info("No alert [%s]: %s", event_id, alert)

    # record for dashboard (includes reasoning text + edge context)
    record = {
        **response,
        "device_id": edge.get("device_id"),
        "edge_confidence": edge.get("edge_confidence"),
        "edge_model": edge.get("edge_model"),
        "frame": frame_name,
        "reasoning_text": result.get("text"),
        "reasoning_available": result.get("available"),
        "latency_s": result.get("latency_s"),
        "received_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
    _record_event(record, frame_name)

    return jsonify(response)


# --- DASHBOARD data + frames ------------------------------------------------
@app.get("/events")
def events():
    """Recent events + verification results, newest first (dashboard polls this)."""
    limit = request.args.get("limit", type=int) or EVENTS_MAX
    with _events_lock:
        items = list(_events)[:limit]
        latest = _latest_frame["name"]
    return jsonify({
        "count": len(items),
        "latest_frame": latest,
        "vlm_available": vlm.status().get("available"),
        "events": items,
    })


@app.get("/frames/<path:name>")
def frames(name):
    return send_from_directory(UPLOAD_DIR, name)


@app.get("/latest.jpg")
def latest_frame():
    with _events_lock:
        name = _latest_frame["name"]
    if not name:
        return jsonify({"error": "no frame received yet"}), 404
    return send_from_directory(UPLOAD_DIR, name)


@app.get("/dashboard")
def dashboard():
    return send_from_directory(STATIC_DIR, "dashboard.html")


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.errorhandler(413)
def too_large(_e):
    return jsonify({"ok": False,
                    "error": f"upload exceeds {MAX_UPLOAD_MB} MB limit"}), 413


def main():
    log.info("=" * 60)
    log.info("Qonclave hub starting on http://%s:%s", HOST, PORT)
    log.info("Static dir : %s", STATIC_DIR)
    log.info("Upload dir : %s", UPLOAD_DIR)
    log.info("VLM status : %s", vlm.status())
    if os.environ.get("QONCLAVE_WARMUP") == "1":
        log.info("QONCLAVE_WARMUP=1 -> loading VLM model now...")
        vlm.warmup()
        log.info("VLM status after warmup: %s", vlm.status())
    log.info("Endpoints  : GET /health | POST /reason | POST /event")
    log.info("             GET /events | GET /latest.jpg | GET /frames/<name>")
    log.info("             GET / (upload test) | GET /dashboard")
    log.info("=" * 60)
    # threaded=True so /health stays responsive; generation is serialized
    # inside the backend via its own lock.
    app.run(host=HOST, port=PORT, threaded=True)


if __name__ == "__main__":
    main()
