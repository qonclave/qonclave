"""
server.py — generic Flask app factory for the Qonclave framework.

Wires the framework's generic HTTP surface (health check, edge event
ingestion, dashboard/frame endpoints, raw VLM reasoning tester) to one app's
Policy. No use-case-specific logic lives here — that's entirely inside the
Policy passed to create_app().

Endpoints:
    GET  /health              liveness + VLM availability
    GET  /                    redirects to /user/dashboard

    GET  /test/               redirects to /test/edge
    GET  /test/edge           edge-device simulator page (visually distinct
                               from the dashboard; linked from it as "Test")
    GET  /test/hub            hub-side MQTT console page (publish/observe any
                               topic); links to /test/edge
    POST /test/mqtt/publish   generic MQTT publish proxy (topic + JSON payload)
    GET  /test/mqtt/messages  recently received MQTT messages (polled by both
                               /test/* pages)
    POST /edge/event          edge event JSON + frame -> policy-driven
                               verification response
    POST /recognize            per-track-id face identification: a single
                               cropped-person JPEG + track_id -> identity
    POST /sms                 Twilio inbound-reply webhook: runs policy
                               on_sms_reply(), optionally publishes MQTT command

    GET  /user/dashboard      live dashboard page (app-provided static/);
                               also the default landing page (/, /user/)
    GET  /user/events         recent events + results (JSON)
    POST /user/robot-command  validate and publish a robot command over MQTT
    GET  /user/latest.jpg     most recent frame
    GET  /user/frames/<name>  a specific stored frame
    POST /user/reason         raw VLM tester (free-form reasoning; no browser
                               page — curl/API only)
    GET  /user/known_faces    names currently enrolled for face-ID
    POST /user/known_faces     enroll a known face (multipart 'image' + 'name')
    GET  /user/recognize_activity        recent POST /recognize calls (JSON)
    GET  /user/recognize_activity/<id>.jpg  the crop for one of those calls

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
import time
import uuid

from flask import Flask, Response, jsonify, redirect, request, send_from_directory

from . import discovery, events, icons, recognize_activity, transport
from .llm import LLMBackend
from .mqtt_bus import MQTTBus
from .policy import Policy
from .sms_bus import SMSBus
from .vlm import VLMBackend

log = logging.getLogger("qonclave.hub")

MAX_UPLOAD_MB = int(os.environ.get("QONCLAVE_MAX_UPLOAD_MB", "16"))


def create_app(policy: Policy, vlm: VLMBackend, mqtt: MQTTBus, sms: SMSBus,
               static_dir: str, face_id=None, llm: LLMBackend | None = None) -> Flask:
    """
    Build the Qonclave hub Flask app for one Policy.

    policy      the app's Policy instance (evaluate/command_for/notify_for)
    vlm         shared VLMBackend, exposed via /health and /user/reason
    mqtt        shared MQTTBus; commands from command_for() are also
                published here so a device can receive them without an
                open HTTP request
    face_id     optional FaceIdentityBackend, exposed via /health only —
                actual identification happens inside the Policy, not here
    sms         shared SMSBus; sends an SMS when notify_for() returns a
                Notification (trial mode: fixed template + fixed number)
    llm         optional LLMBackend (text-only Qwen3-4B); used by the Policy
                for on_sms_reply() reasoning; exposed via /health
    static_dir  directory holding the app's dashboard.html, test_*.html
    """
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

    icons.load_cache()
    icons.start_boot_warming(vlm)
    http_port = int(os.environ.get("QONCLAVE_PORT", "8000"))
    discovery.start_broadcaster(http_port=http_port)

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
            "llm": llm.status() if llm else {"available": False},
            "mqtt": mqtt.status(),
            "face_id": face_id.status() if face_id else {"available": False},
            "sms": sms.status(),
        })

    @app.get("/")
    def root():
        return redirect("/user/dashboard", code=302)

    @app.errorhandler(413)
    def too_large(_e):
        return jsonify({"ok": False,
                        "error": f"upload exceeds {MAX_UPLOAD_MB} MB limit"}), 413

    # --- /test/*: standalone device simulator + MQTT console ---------------
    @app.get("/test/")
    @app.get("/test")
    def test_index():
        return redirect("/test/edge", code=302)

    @app.get("/test/edge")
    def edge_simulator():
        # Simulates a device; linked from the dashboard, but visually
        # distinct (see the "Test" badge) so it reads as a dev tool.
        return send_from_directory(static_dir, "test_edge.html")

    @app.get("/test/hub")
    def hub_mqtt_console():
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

        notification = policy.notify_for(verdict, event)
        if notification is not None:
            sms.send(notification)

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
        record = {
            **response,
            "device_id": event.get("device_id"),
            "edge_confidence": event.get("edge_confidence"),
            "edge_model": event.get("edge_model"),
            "frame": frame_name,
            "reasoning_text": verdict.reasoning_text,
            "reasoning_available": verdict.reasoning_available,
            "latency_s": verdict.latency_s,
            "received_at": transport.now_iso(),
        }
        events.record_event(record, frame_name)

        # persist the VLM/verification result next to the frame as
        # <frame>.json, so stored frames carry their result on disk (and are
        # fetchable via /user/frames/<frame>.json) — not just in the in-memory
        # ring buffer the dashboard reads.
        sidecar = transport.save_result_sidecar(frame_name, record)
        if sidecar:
            log.info("Saved result sidecar -> %s", os.path.basename(sidecar))

        return jsonify(response)

    @app.route("/edge/icon", methods=["GET", "POST"])
    def edge_icon():
        """Device contract: retrieve or synthesize Level 2 cached 12x8 icon bitmap."""
        label = request.args.get("label", "clear").lower().strip()
        client = request.remote_addr
        log.info("%s /edge/icon?label=%s from %s", request.method, label, client)

        image_path = None
        if request.method == "POST" and request.content_length and request.content_length > 0:
            path, err = transport.save_incoming_image()
            if not err and path:
                image_path = path

        entry = icons.get_or_generate_icon(label, vlm, image_path)
        return jsonify({
            "ok": True,
            "label": label,
            "bitmap": entry.get("bitmap"),
            "updated_at": entry.get("updated_at"),
            "permanent": entry.get("permanent", False)
        })

    # --- /recognize: per-track-id face identification -----------------------
    @app.post("/recognize")
    def recognize():
        """Device contract: identify the single cropped person in the
        uploaded image, tagged with the edge's own track_id, against the
        known_faces/ database.

        Request:  multipart 'image' file (or raw image body) + 'track_id'
                   (form field, query param, or JSON field).
        Response: {"track_id": int, "identity": str, "confidence": float,
                    "status": "known"|"unknown"|"no_face"|"unavailable"}
        The crop is deleted from disk right after inference, unlike
        /edge/event's frames which are kept permanently for the dashboard —
        a short-lived copy is kept in memory only, in recognize_activity's
        capped ring buffer, so the dashboard can show recent activity without
        persisting every sampled crop to disk.
        """
        client = request.remote_addr

        raw_track_id = request.form.get("track_id")
        if raw_track_id is None:
            raw_track_id = request.args.get("track_id")
        if raw_track_id is None and request.is_json:
            raw_track_id = (request.get_json(silent=True) or {}).get("track_id")
        if raw_track_id is None:
            return jsonify({"ok": False, "error": "missing 'track_id'"}), 400
        try:
            track_id = int(raw_track_id)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "'track_id' must be an integer"}), 400

        if face_id is None:
            return jsonify({"track_id": track_id, "identity": "unavailable",
                             "confidence": 0.0, "status": "unavailable"}), 503

        path, err = transport.save_incoming_image()
        if err:
            log.warning("POST /recognize rejected from %s (track_id=%s): %s",
                        client, track_id, err)
            return jsonify({"ok": False, "error": err}), 400

        t0 = time.monotonic()
        try:
            with open(path, "rb") as f:
                image_bytes = f.read()
            result = face_id.identify(path)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        latency_ms = (time.monotonic() - t0) * 1000

        if not result.get("available"):
            identity, status, confidence = "unavailable", "unavailable", 0.0
        elif not result.get("face_detected"):
            identity, status, confidence = "no_face", "no_face", 0.0
        elif result.get("identified"):
            identity, status = result.get("name"), "known"
            confidence = result.get("confidence") or 0.0
        else:
            identity, status = "unknown", "unknown"
            confidence = result.get("confidence") or 0.0

        log.info("POST /recognize track_id=%s -> %s%s (%.1f%%, %.0fms) from %s",
                 track_id, status, f" ({identity})" if status == "known" else "",
                 confidence * 100, latency_ms, client)

        recognize_activity.record(
            track_id, identity, round(float(confidence), 4), status,
            latency_ms, image_bytes, source_ip=client,
        )

        return jsonify({
            "track_id": track_id,
            "identity": identity,
            "confidence": round(float(confidence), 4),
            "status": status,
        })

    # --- /sms: Twilio inbound-reply webhook ---------------------------------
    @app.post("/sms")
    def sms_reply():
        """
        Twilio webhook: called when the recipient replies to an outbound SMS.
        Twilio POSTs form fields; we read From + Body, hand them to the
        Policy, and publish any returned MQTT command to the last known device.
        Signature validation is skipped in trial mode.
        """
        sender = request.form.get("From", "").strip()
        body = request.form.get("Body", "").strip()
        log.info("SMS reply from %s: %r", sender, body)

        command = policy.on_sms_reply(sender, body)
        if command is not None:
            device_id = events.latest_device_id()
            if device_id:
                mqtt.publish_command(device_id, command)
                log.info("SMS reply MQTT command %s -> device %s", command, device_id)
                action = "mqtt_published"
            else:
                log.warning("SMS reply returned command %s but no device_id known yet", command)
                action = "ignored"
        elif body.strip().upper() == "STOP":
            action = "suppressed"
        else:
            action = "ignored"

        reply_text = policy.reply_for_sms(sender, body)
        if reply_text:
            from .policy import Notification
            sent = sms.send(Notification(message=reply_text, recipient=sender))
            log.info("SMS reply_for_sms -> sent=%s: %r", sent, reply_text[:80])

        sms.record_reply(sender, body, action)
        return ("", 200)

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

    @app.get("/user/recognize_activity")
    def user_recognize_activity():
        """Recent POST /recognize calls (track_id, identity, confidence,
        status), newest first — what's actually arriving at the hub for
        per-track face recognition. Distinct from /user/events, which is
        /edge/event's whole-frame ring buffer."""
        limit = request.args.get("limit", type=int) or 20
        items = recognize_activity.recent(limit)
        return jsonify({"count": len(items), "activity": items})

    @app.get("/user/recognize_activity/<int:entry_id>.jpg")
    def user_recognize_image(entry_id):
        image = recognize_activity.get_image(entry_id)
        if image is None:
            return jsonify({"error": "not found or already evicted"}), 404
        return Response(image, mimetype="image/jpeg")

    @app.post("/user/robot-command")
    def user_robot_command():
        """Publish a validated dashboard robot command to one edge device."""
        body = request.get_json(silent=True) or {}
        device_id = str(body.get("device_id") or events.latest_device_id() or "").strip()
        direction = str(body.get("direction") or "").strip().upper()

        if not device_id:
            return jsonify({"ok": False, "error": "no edge device selected"}), 400
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", device_id):
            return jsonify({"ok": False, "error": "invalid device_id"}), 400
        if direction not in {"LEFT", "RIGHT", "FORWARD", "BACKWARD", "STOP"}:
            return jsonify({"ok": False, "error": "invalid direction"}), 400

        try:
            magnitude = int(body.get("magnitude", 1))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "magnitude must be an integer"}), 400
        if not 1 <= magnitude <= 360:
            return jsonify({"ok": False, "error": "magnitude must be between 1 and 360"}), 400

        command = {
            "type": "robot_move",
            "direction": direction,
            "magnitude": magnitude,
        }
        ok = mqtt.publish_command(device_id, command)
        if not ok:
            return jsonify({
                "ok": False,
                "error": "MQTT broker unavailable or publish failed",
                "device_id": device_id,
            }), 503

        log.info("Dashboard robot command %s -> device %s", command, device_id)
        return jsonify({"ok": True, "device_id": device_id, "command": command})

    @app.post("/user/buzzer-command")
    def user_buzzer_command():
        """Publish a validated dashboard buzzer command to one edge device."""
        body = request.get_json(silent=True) or {}
        device_id = str(body.get("device_id") or events.latest_device_id() or "buzzer-01").strip()
        action = str(body.get("action") or "").strip().lower()

        if not device_id:
            return jsonify({"ok": False, "error": "no edge device selected"}), 400
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", device_id):
            return jsonify({"ok": False, "error": "invalid device_id"}), 400
        if action not in {"start", "stop", "tone", "believer", "song"}:
            return jsonify({"ok": False, "error": "action must be 'start', 'stop', 'tone', 'believer', or 'song'"}), 400

        try:
            frequency = int(body.get("frequency", 440))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "frequency must be an integer"}), 400
        if not 20 <= frequency <= 20000:
            return jsonify({"ok": False, "error": "frequency must be between 20 and 20000 Hz"}), 400

        try:
            duration = int(body.get("duration", 0))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "duration must be an integer"}), 400
        if duration < 0:
            return jsonify({"ok": False, "error": "duration must be >= 0 ms"}), 400

        command = {
            "type": "buzzer",
            "action": action,
            "frequency": frequency,
            "duration": duration,
        }
        ok = mqtt.publish_command(device_id, command)
        if not ok:
            return jsonify({
                "ok": False,
                "error": "MQTT broker unavailable or publish failed",
                "device_id": device_id,
            }), 503

        log.info("Dashboard buzzer command %s -> device %s", command, device_id)
        return jsonify({"ok": True, "device_id": device_id, "command": command})

    @app.get("/user/frames/<path:name>")
    def user_frame(name):
        return send_from_directory(transport.UPLOAD_DIR, name)

    @app.get("/user/latest.jpg")
    def user_latest_frame():
        name = events.latest_frame_name()
        if not name:
            return jsonify({"error": "no frame received yet"}), 404
        return send_from_directory(transport.UPLOAD_DIR, name)

    @app.get("/user/sms_activity")
    def user_sms_activity():
        """Recent SMS activity (outbound + inbound), newest first."""
        limit = request.args.get("limit", type=int) or 50
        return jsonify({
            "count": len(sms.recent_activity(limit)),
            "suppressed": sms._suppressed,
            "activity": sms.recent_activity(limit),
        })

    @app.get("/user/llm_response")
    def user_llm_response():
        """Latest LLM analysis of an inbound SMS reply, for the dashboard."""
        analysis = policy.last_sms_analysis()
        return jsonify({
            "available": (llm.status().get("available") if llm else False),
            "analysis": analysis,
        })

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

    # --- /user/known_faces: enroll people for face-ID ----------------------
    @app.get("/user/known_faces")
    def list_known_faces():
        """Names currently enrolled (drives the dashboard's roster)."""
        names = face_id.known_names() if face_id else []
        return jsonify({
            "available": bool(face_id and face_id.status().get("available")),
            "count": len(names),
            "names": names,
        })

    @app.post("/user/known_faces")
    def enroll_known_face():
        """Add a known face: multipart 'image' + a 'name' field. The next
        inference run will match against the newly enrolled person."""
        client = request.remote_addr
        if face_id is None:
            return jsonify({"ok": False, "error": "face ID not enabled on this hub"}), 501

        name = (request.form.get("name") or request.args.get("name") or "").strip()
        if not name:
            return jsonify({"ok": False, "error": "missing 'name'"}), 400

        path, err = transport.save_incoming_image()
        if err:
            log.warning("POST /user/known_faces rejected from %s: %s", client, err)
            return jsonify({"ok": False, "error": err}), 400

        result = face_id.enroll(name, path)
        # The uploaded copy in uploads/ was only a staging file; enroll() has
        # written its own copy into known_faces/, so drop the staging one.
        try:
            os.remove(path)
        except OSError:
            pass

        if not result.get("ok"):
            log.warning("Enroll failed for '%s' from %s: %s", name, client, result.get("error"))
            return jsonify(result), 400

        log.info("Enrolled '%s' (slug=%s) from %s", name, result.get("slug"), client)
        return jsonify({**result, "names": face_id.known_names()})

    # --- app pages (served from the app's own static_dir) -------------------
    @app.get("/user/dashboard")
    def user_dashboard():
        return send_from_directory(static_dir, "dashboard.html")

    @app.get("/user/")
    @app.get("/user")
    def user_index():
        # default landing = dashboard
        return send_from_directory(static_dir, "dashboard.html")

    return app
