"""
server.py — generic Flask app factory for the Qonclave framework.

Wires the framework's generic HTTP surface (health check, edge event
ingestion, dashboard/frame endpoints, raw VLM reasoning tester) to one app's
Policy. No use-case-specific logic lives here — that's entirely inside the
Policy passed to create_app().

Endpoints:
    GET  /health              liveness + VLM availability
    GET  /                    redirects to /user/dashboard

    GET  /test/edge           edge-device simulator page (standalone, unlinked
                               from the operator UI — visually distinct)
    GET  /test/hub            hub-side MQTT console page (publish/observe any
                               topic); links only to /test/edge, not /user/*
    POST /test/mqtt/publish   generic MQTT publish proxy (topic + JSON payload)
    GET  /test/mqtt/messages  recently received MQTT messages (polled by both
                               /test/* pages)
    POST /edge/event          edge event JSON + frame -> policy-driven
                               verification response

    GET  /user/dashboard      live dashboard page (app-provided static/);
                               also the default landing page (/, /user/)
    GET  /user/events         recent events + results (JSON)
    GET  /user/latest.jpg     most recent frame
    GET  /user/frames/<name>  a specific stored frame
    POST /user/reason         raw VLM tester (free-form reasoning)
    GET  /user/test_reason    reasoning tester page

Design goals:
    * Runs on ANY laptop (regular x86 Windows/Linux included). Reasoning is
      conditional — see framework/vlm.py — so only that piece is
      Snapdragon-only. Everything else runs anywhere.
    * Edge-device friendly: /edge/event accepts BOTH multipart form uploads
      and raw image bodies.
    * Everything is logged to the terminal where the server runs.
"""

from __future__ import annotations

import logging
import os
import re
import uuid

from flask import Flask, jsonify, redirect, request, send_from_directory

from . import events, transport
from .mqtt_bus import MQTTBus
from .policy import Policy
from .vlm import VLMBackend

log = logging.getLogger("qonclave.hub")

MAX_UPLOAD_MB = int(os.environ.get("QONCLAVE_MAX_UPLOAD_MB", "16"))


def create_app(policy: Policy, vlm: VLMBackend, mqtt: MQTTBus, static_dir: str) -> Flask:
    """
    Build the Qonclave hub Flask app for one Policy.

    policy      the app's Policy instance (evaluate/command_for)
    vlm         shared VLMBackend, exposed via /health and /user/reason
    mqtt        shared MQTTBus; commands from command_for() are also
                published here so a device can receive them without an
                open HTTP request
    static_dir  directory holding the app's dashboard.html, test_*.html
    """
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

    # --- /health, / --------------------------------------------------------
    @app.get("/health")
    def health():
        # debug-level: the test pages poll this every 15s; don't spam the console
        log.debug("GET /health from %s", request.remote_addr)
        return jsonify({
            "status": "ok",
            "service": "qonclave-hub",
            "app": policy.name,
            "time": transport.now_iso(),
            "vlm": vlm.status(),
            "mqtt": mqtt.status(),
        })

    @app.get("/")
    def root():
        return redirect("/user/dashboard", code=302)

    @app.errorhandler(413)
    def too_large(_e):
        return jsonify({"ok": False,
                        "error": f"upload exceeds {MAX_UPLOAD_MB} MB limit"}), 413

    # --- /test/*: standalone device simulator + MQTT console ---------------
    @app.get("/test/edge")
    def edge_simulator():
        # Deliberately not linked from /user/* — this simulates a device,
        # it isn't part of the operator-facing app.
        return send_from_directory(static_dir, "test_edge.html")

    @app.get("/test/hub")
    def hub_mqtt_console():
        # Same isolation as /test/edge — links only to /test/edge, never /user/*.
        return send_from_directory(static_dir, "test_hub.html")

    @app.post("/test/mqtt/publish")
    def mqtt_publish():
        body = request.get_json(silent=True) or {}
        topic = body.get("topic")
        payload = body.get("payload")
        if not topic:
            return jsonify({"ok": False, "error": "missing 'topic'"}), 400
        ok = mqtt.publish(topic, payload if payload is not None else {})
        return jsonify({"ok": ok, "mqtt_available": mqtt.is_available()})

    @app.get("/test/mqtt/messages")
    def mqtt_messages():
        topic_filter = request.args.get("topic")
        limit = request.args.get("limit", type=int) or 50
        if topic_filter:
            mqtt.subscribe(topic_filter)
        messages = mqtt.recent_messages(limit)
        if topic_filter:
            pattern = topic_filter.replace("+", "[^/]+").replace("#", ".*")
            regex = re.compile(f"^{pattern}$")
            messages = [m for m in messages if regex.match(m["topic"])]
        return jsonify({"mqtt_available": mqtt.is_available(), "messages": messages})

    @app.post("/edge/event")
    def edge_event():
        """Device contract: ingest frame + event, run policy, record, respond."""
        client = request.remote_addr
        log.info("POST /edge/event from %s (content-type=%s, len=%s)",
                 client, request.headers.get("Content-Type"), request.content_length)

        event = transport.parse_edge_event()

        path, err = transport.save_incoming_image()
        if err:
            log.warning("POST /edge/event rejected from %s: %s", client, err)
            return jsonify({"received": False, "error": err}), 400

        event_id = event.get("event_id") or f"{transport.timestamp()}-{uuid.uuid4().hex[:8]}"
        frame_name = os.path.basename(path)
        log.info("Edge event %s | device=%s | edge_conf=%s | frame=%s",
                 event_id, event.get("device_id"), event.get("edge_confidence"), frame_name)

        verdict = policy.evaluate(path, event)
        command = policy.command_for(verdict, event)
        device_id = event.get("device_id")
        if command is not None and device_id:
            mqtt.publish_command(device_id, command)

        response = {
            "schema_version": events.SCHEMA_VERSION,
            "event_id": event_id,
            "received": True,
            "hub_verified": verdict.verified,
            "hub_confidence": verdict.confidence,
            "alert": verdict.alert,
            "command": command,
            **verdict.extra,
        }

        if verdict.verified:
            log.info("ALERT [%s]: %s", event_id, verdict.alert)
        else:
            log.info("No alert [%s]: %s", event_id, verdict.alert)

        # record for the dashboard (includes reasoning text + edge context)
        events.record_event({
            **response,
            "device_id": event.get("device_id"),
            "edge_confidence": event.get("edge_confidence"),
            "edge_model": event.get("edge_model"),
            "frame": frame_name,
            "reasoning_text": verdict.reasoning_text,
            "reasoning_available": verdict.reasoning_available,
            "latency_s": verdict.latency_s,
            "received_at": transport.now_iso(),
        }, frame_name)

        return jsonify(response)

    # --- /user/* dashboard data + frames ------------------------------------
    @app.get("/user/events")
    def user_events():
        """Recent events + verification results, newest first."""
        limit = request.args.get("limit", type=int)
        items, latest = events.recent_events(limit)
        return jsonify({
            "count": len(items),
            "latest_frame": latest,
            "vlm_available": vlm.status().get("available"),
            "events": items,
        })

    @app.get("/user/frames/<path:name>")
    def user_frame(name):
        return send_from_directory(transport.UPLOAD_DIR, name)

    @app.get("/user/latest.jpg")
    def user_latest_frame():
        name = events.latest_frame_name()
        if not name:
            return jsonify({"error": "no frame received yet"}), 404
        return send_from_directory(transport.UPLOAD_DIR, name)

    # --- /user/reason: raw VLM tester ---------------------------------------
    @app.post("/user/reason")
    def user_reason():
        client = request.remote_addr
        prompt = transport.request_prompt(default_prompt=None)  # falls back inside vlm.reason()
        log.info("POST /user/reason from %s (content-type=%s, len=%s)",
                 client, request.headers.get("Content-Type"), request.content_length)

        path, err = transport.save_incoming_image()
        if err:
            log.warning("POST /user/reason rejected from %s: %s", client, err)
            return jsonify({"ok": False, "error": err}), 400

        log.info("Saved upload -> %s ; running reasoning...", path)
        result = vlm.reason(path, prompt=prompt)

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

    # --- app pages (served from the app's own static_dir) -------------------
    @app.get("/user/dashboard")
    def user_dashboard():
        return send_from_directory(static_dir, "dashboard.html")

    @app.get("/user/test_reason")
    def user_test_reason():
        return send_from_directory(static_dir, "test_reason.html")

    @app.get("/user/")
    @app.get("/user")
    def user_index():
        # default landing = dashboard
        return send_from_directory(static_dir, "dashboard.html")

    return app
