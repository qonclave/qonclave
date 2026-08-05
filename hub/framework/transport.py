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
from qonclave.core.models import EdgeEvent

from . import adapter

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


def save_incoming_image(event: "EdgeEvent | None" = None) -> tuple[str | None, str | None]:
    """
    Extract an image from the request and save it to UPLOAD_DIR.
    Supports three shapes:
      1. multipart/form-data with a file field named "image" (browsers, curl -F)
      2. raw image bytes as the request body with Content-Type image/*
         (dead simple for constrained edge devices)
      3. base64 inside a spec/v1 document's `payload` — pass the parsed `event`

    Shapes 1 and 2 are checked first, so a device that already streams the frame
    as the body pays nothing for the existence of shape 3. Base64 costs ~33% on
    a JPEG, which is why it is the fallback rather than the default even though
    it is the shape the schema describes.

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

    # Shape 3: base64 inside the spec document's payload
    if event is not None:
        data = adapter.payload_bytes(event)
        if data:
            media_type = (event.payload.media_type if event.payload else "") or ""
            ext = _ext_from_content_type(media_type)
            name = f"{timestamp()}-{uuid.uuid4().hex[:8]}.{ext}"
            path = os.path.join(UPLOAD_DIR, name)
            with open(path, "wb") as fh:
                fh.write(data)
            return path, None

    # Nothing was offered at all. That is a legal event, not an error: the spec
    # makes `payload` optional because a threshold crossing from a sensor is a
    # real observation with nothing to look at. The Policy decides what to do
    # with it.
    #
    # An error is reserved for the case where a frame WAS offered and could not
    # be used — an empty file field, or an undecodable payload. Collapsing the
    # two would mean a device whose camera silently stopped attaching frames
    # looks identical to one that never had a camera.
    if not frame_was_offered(event):
        return None, None

    return None, (
        "a frame was offered but could not be read. Send multipart form field "
        "'image', POST raw image bytes with Content-Type image/jpeg, or include "
        "a base64 `payload` in a spec/v1 event document."
    )


def frame_was_offered(event: "EdgeEvent | None" = None) -> bool:
    """Whether this request intended to carry a frame, however badly."""
    if "image" in request.files:
        return True
    ct = request.headers.get("Content-Type", "")
    if request.data and (ct.startswith("image/") or "octet-stream" in ct):
        return True
    return event is not None and event.payload is not None


def result_sidecar_path(frame_name: str) -> str:
    """Path of the JSON sidecar for a stored frame: '<frame>.json', living
    next to the frame in UPLOAD_DIR so /user/frames/<name> serves it too."""
    return os.path.join(UPLOAD_DIR, f"{frame_name}.json")


def save_result_sidecar(frame_name: str, result: dict) -> str | None:
    """Persist the hub's verification/VLM result next to its frame as
    '<frame>.json'. Best-effort: logs and returns None on failure (a disk
    error must never break the /edge/event response). Returns the path on
    success."""
    if not frame_name:
        return None
    path = result_sidecar_path(frame_name)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, ensure_ascii=False, default=str)
        return path
    except OSError as e:
        log.warning("could not write result sidecar %s: %s", path, e)
        return None


def parse_edge_event() -> "EdgeEvent":
    """
    Extract the edge event that accompanies a frame, as a validated EdgeEvent.

    Tolerant of how a constrained device sends it — the four shapes below are
    unchanged, and a fifth was added for spec-format documents:
      * multipart field "event" containing a JSON string
      * individual multipart form fields (device_id, event_id, edge_confidence…)
      * query-string params (?device_id=…&edge_confidence=…) for raw-body POSTs
      * request JSON body (when no file part)
      * a spec/v1 JSON body (source_node_id / trigger / …), detected by shape

    Missing fields are simply absent; nothing here raises. `adapter` decides
    which vocabulary arrived and normalises it either way, so callers above this
    line only ever see one model.
    """
    return adapter.to_edge_event(parse_edge_event_raw())


def parse_edge_event_raw() -> dict:
    """The untranslated dict, exactly as the four legacy shapes produced it.

    Kept separate so the extraction logic and the vocabulary translation can be
    tested independently — a bug in "where did the field come from" and a bug in
    "what is the field called" have different fixes.
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

    # d) a full JSON body — either legacy flat keys, or a spec/v1 document.
    #    Merged rather than gated on `not ev` for the spec case: a device may
    #    legitimately send the document as the body while identifying itself in
    #    the query string, and dropping the body there would lose the event.
    if request.is_json:
        body = request.get_json(silent=True) or {}
        if isinstance(body, dict) and (not ev or adapter.looks_like_spec(body)):
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
