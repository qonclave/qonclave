"""
transport.py — generic device<->hub transport for the Qonclave framework.

Use-case agnostic: extracting an uploaded frame and the accompanying edge
event metadata from an HTTP request. Tolerant of how a constrained device
sends data (multipart form, raw image body, query string) so any app built
on this framework gets the same flexible ingestion for free.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import uuid

from flask import request

log = logging.getLogger("qonclave.hub")

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "uploads")
UPLOAD_DIR = os.path.normpath(UPLOAD_DIR)
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXT = {"jpg", "jpeg", "png", "bmp", "webp"}

EDGE_EVENT_FIELDS = (
    "device_id", "event_id", "event_type", "edge_model",
    "edge_confidence", "threshold", "frame_id", "created_at",
)


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
         (dead simple for constrained edge devices)
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

    # Shape 2: raw body (constrained-device friendly)
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

    # a) a JSON blob in form field "event"
    raw = request.form.get("event")
    if raw:
        try:
            ev.update(json.loads(raw))
        except (ValueError, TypeError):
            log.warning("event field was not valid JSON; ignoring")

    # b) individual form fields
    for k in EDGE_EVENT_FIELDS:
        if k in request.form and k not in ev:
            ev[k] = request.form.get(k)

    # c) query params (handy for raw-body edge-device POSTs)
    for k in EDGE_EVENT_FIELDS:
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


def request_prompt(default_prompt: str) -> str:
    return (request.form.get("prompt") or request.args.get("prompt")
            or request.headers.get("X-Prompt") or default_prompt)
