"""
user_routes.py — human-facing endpoints (browser / operator).

All routes are under the /user prefix:

    GET  /user/dashboard        live event + verification dashboard page
    GET  /user/events           recent events + results (dashboard polls this)
    GET  /user/latest.jpg       most recent escalation frame
    GET  /user/frames/<name>    a specific stored frame
    POST /user/reason           raw VLM tester: image in, reasoning text out
    GET  /user/  (and /user)    image upload test page
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify, request, send_from_directory

import state

log = logging.getLogger("qonclave.hub")

user_bp = Blueprint("user", __name__, url_prefix="/user")


# --- dashboard data + frames ------------------------------------------------
@user_bp.get("/events")
def events():
    """Recent events + verification results, newest first."""
    limit = request.args.get("limit", type=int)
    items, latest = state.recent_events(limit)
    return jsonify({
        "count": len(items),
        "latest_frame": latest,
        "vlm_available": state.vlm.status().get("available"),
        "events": items,
    })


@user_bp.get("/frames/<path:name>")
def frames(name):
    return send_from_directory(state.UPLOAD_DIR, name)


@user_bp.get("/latest.jpg")
def latest_frame():
    name = state.latest_frame_name()
    if not name:
        return jsonify({"error": "no frame received yet"}), 404
    return send_from_directory(state.UPLOAD_DIR, name)


# --- raw VLM tester ---------------------------------------------------------
@user_bp.post("/reason")
def reason():
    client = request.remote_addr
    prompt = state.request_prompt()
    log.info("POST /user/reason from %s (content-type=%s, len=%s)",
             client, request.headers.get("Content-Type"), request.content_length)

    path, err = state.save_incoming_image()
    if err:
        log.warning("POST /user/reason rejected from %s: %s", client, err)
        return jsonify({"ok": False, "error": err}), 400

    log.info("Saved upload -> %s ; running reasoning...", path)
    result = state.vlm.reason(path, prompt=prompt)

    if result.get("error") and not result.get("available"):
        log.warning("Reasoning unavailable: %s", result["error"])
    elif result.get("error"):
        log.error("Reasoning error: %s", result["error"])
    else:
        preview = (result.get("text") or "")[:200].replace("\n", " ")
        log.info("Reasoning result (%ss): %s", result.get("latency_s"), preview)

    return jsonify({
        "ok": result.get("error") is None,
        "image_saved_as": os.path.basename(path),
        **result,
    })


# --- pages ------------------------------------------------------------------
@user_bp.get("/dashboard")
def dashboard():
    return send_from_directory(state.STATIC_DIR, "dashboard.html")


@user_bp.get("/")
def index():
    return send_from_directory(state.STATIC_DIR, "index.html")
