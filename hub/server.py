"""
server.py — Qonclave hub HTTP server.

Endpoints:
    GET  /health          -> liveness + VLM availability
    POST /reason          -> accept an image, run VLM reasoning, return JSON
    GET  /                -> test upload webpage
    GET  /static/...      -> static assets

Design goals:
    * Runs on ANY laptop (regular x86 Windows/Linux included). The reasoning
      part is conditional — see hub/vlm_backend.py — so only that piece is
      Snapdragon-only. Everything else (upload, logging, webpage) is testable
      anywhere.
    * Arduino UNO Q friendly: /reason accepts BOTH a normal multipart form
      upload (field name "image") AND a raw image body (Content-Type image/*),
      which is the simplest thing to POST from a constrained device.
    * Everything is logged to the terminal where the server runs.

Run:
    pip install -r hub/requirements.txt
    python hub/server.py                 # http://0.0.0.0:8000
    # options via env: QONCLAVE_HOST, QONCLAVE_PORT, QONCLAVE_WARMUP=1
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import sys
import uuid

from flask import (
    Flask, Response, jsonify, request, send_from_directory,
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
    log.info("Endpoints  : GET /health | POST /reason | GET / (test page)")
    log.info("=" * 60)
    # threaded=True so /health stays responsive; generation is serialized
    # inside the backend via its own lock.
    app.run(host=HOST, port=PORT, threaded=True)


if __name__ == "__main__":
    main()
