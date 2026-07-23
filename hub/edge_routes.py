"""
edge_routes.py — device-facing endpoints (Arduino UNO Q -> hub).

All routes are under the /edge prefix. These are the machine-to-machine
contract: a constrained edge device escalates a frame + event metadata and gets
back a schema-compliant verification response (qonclave_plan.md §5.3).

    POST /edge/event   escalation frame + edge event -> verification response
"""

from __future__ import annotations

import logging
import os
import uuid

from flask import Blueprint, jsonify, request

import state

log = logging.getLogger("qonclave.hub")

edge_bp = Blueprint("edge", __name__, url_prefix="/edge")


@edge_bp.post("/event")
def event():
    """Real edge contract: ingest frame + event, verify, record, respond."""
    client = request.remote_addr
    log.info("POST /edge/event from %s (content-type=%s, len=%s)",
             client, request.headers.get("Content-Type"), request.content_length)

    edge = state.parse_edge_event()
    prompt = state.request_prompt()

    path, err = state.save_incoming_image()
    if err:
        log.warning("POST /edge/event rejected from %s: %s", client, err)
        return jsonify({"received": False, "error": err}), 400

    event_id = edge.get("event_id") or f"{state.timestamp()}-{uuid.uuid4().hex[:8]}"
    frame_name = os.path.basename(path)
    log.info("Edge event %s | device=%s | edge_conf=%s | frame=%s",
             event_id, edge.get("device_id"), edge.get("edge_confidence"), frame_name)

    result = state.vlm.reason(path, prompt=prompt)
    hub_verified, hub_conf, alert = state.verify_from_reasoning(result)

    # schema-compliant response (plan §5.3)
    response = {
        "schema_version": state.SCHEMA_VERSION,
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

    # record for the dashboard (includes reasoning text + edge context)
    state.record_event({
        **response,
        "device_id": edge.get("device_id"),
        "edge_confidence": edge.get("edge_confidence"),
        "edge_model": edge.get("edge_model"),
        "frame": frame_name,
        "reasoning_text": result.get("text"),
        "reasoning_available": result.get("available"),
        "latency_s": result.get("latency_s"),
        "received_at": state.now_iso(),
    }, frame_name)

    return jsonify(response)
