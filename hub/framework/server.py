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
    POST /track/analyze       per-track-id analysis: one cropped-person JPEG
                               + track_id + analyzers -> face identity and/or
                               pose keypoints
    POST /sms                 Twilio inbound-reply webhook: runs policy
                               on_reply(), optionally publishes MQTT command

    GET  /user/dashboard      live dashboard page (app-provided static/);
                               also the default landing page (/, /user/)
    GET  /user/network        network page: this hub + devices seen on the LAN
    GET  /user/devices        devices seen on the network (JSON)
    GET  /user/events         recent events + results (JSON)
    POST /user/robot-command  validate and publish a robot command over MQTT
    GET  /user/latest.jpg     most recent frame
    GET  /user/frames/<name>  a specific stored frame
    POST /user/reason         raw VLM tester (free-form reasoning; no browser
                               page — curl/API only)
    GET  /user/known_faces    names currently enrolled for face-ID
    POST /user/known_faces     enroll a known face (multipart 'image' + 'name')
    GET  /user/tracks         live per-track identity + latest pose (JSON)
    GET  /user/tracks/<id>.jpg           latest annotated frame for one track
    GET  /user/recognize_activity        recent analysis calls (JSON)
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

from . import adapter, device_registry, discovery, events, icons, recognize_activity, track_store, transport
from .pose import overlay as pose_overlay
from .llm import LLMBackend
from .mqtt_bus import MQTTBus
from .policy import Policy
from .sms_bus import SMSBus
from .vlm import VLMBackend

log = logging.getLogger("qonclave.hub")

MAX_UPLOAD_MB = int(os.environ.get("QONCLAVE_MAX_UPLOAD_MB", "16"))

# Base path for the spec surface, from `servers` in spec/v1/openapi/hub.yaml.
# Pre-spec routes (/edge/event, /health, /track/analyze, /user/*, /test/*) keep
# their unprefixed names; because the spec surface is prefixed, the two sets
# are disjoint and can coexist without ambiguity.
API_PREFIX = "/api/v1"

# Spec endpoints this hub does not implement yet. Each answers 501 naming the
# reason rather than 404, because "not built here" and "no such endpoint in the
# protocol" are different answers and a client probing for capabilities should
# be able to tell them apart.
NOT_IMPLEMENTED = {
    "checkin": "duty-cycle check-in; no duty-cycled device exists in this deployment",
    "capabilities": "node manifest; discovery still uses the UDP broadcaster",
    "grants": "capability grants; this deployment is single-hub, single-tenant",
}

# Annotated per-track frames. Storing imagery on the hub is exactly what the
# privacy cascade otherwise avoids, so this is opt-out-able, capped, and
# gitignored. Set to 0 for any non-demo deployment.
TRACK_FRAMES_ENABLED = os.environ.get("QONCLAVE_TRACK_FRAMES_ENABLED", "1") == "1"
TRACK_FRAMES_MAX = int(os.environ.get("QONCLAVE_TRACK_FRAMES_MAX", "50"))
TRACK_FRAMES_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "track_frames"))


def _parse_person_box(raw: str | None):
    """Parse "x1,y1,x2,y2" — the person's rect inside the crop.

    Optional by design. The edge's crop is framed for face detection, so the
    person fills only about half of it; this rect lets pose re-frame to what a
    top-down model expects. Absent or malformed, pose uses the whole crop, which
    is worse but not wrong.
    """
    if not raw:
        return None
    try:
        parts = [int(float(p)) for p in str(raw).split(",")]
    except (TypeError, ValueError):
        log.warning("ignoring malformed person_box %r", raw)
        return None
    if len(parts) != 4:
        log.warning("ignoring person_box with %d values, expected 4", len(parts))
        return None
    x1, y1, x2, y2 = parts
    if x2 <= x1 or y2 <= y1:
        log.warning("ignoring inverted person_box %r", raw)
        return None
    return (x1, y1, x2, y2)


def _run_face(face_id, path: str) -> dict:
    """Face identification in the status vocabulary the edge already parses:
    known | unknown | no_face | unavailable."""
    if face_id is None:
        return {"identity": "unavailable", "confidence": 0.0, "status": "unavailable"}

    result = face_id.identify(path)
    if not result.get("available"):
        return {"identity": "unavailable", "confidence": 0.0, "status": "unavailable"}
    if not result.get("face_detected"):
        return {"identity": "no_face", "confidence": 0.0, "status": "no_face"}
    confidence = round(float(result.get("confidence") or 0.0), 4)
    if result.get("identified"):
        return {"identity": result.get("name"), "confidence": confidence, "status": "known"}
    return {"identity": "unknown", "confidence": confidence, "status": "unknown"}


def _run_pose(pose, path: str, person_box) -> dict:
    """Pose estimation: ok | no_pose | unavailable."""
    if pose is None:
        return {"status": "unavailable", "keypoints": None,
                "mean_score": None, "error": "pose backend not enabled on this hub"}

    result = pose.estimate(path, person_box)
    return {
        "status": result.get("status", "unavailable"),
        "keypoints": result.get("keypoints"),
        "mean_score": result.get("mean_score"),
        "error": result.get("error"),
    }


def _save_track_frame(track_id: int, jpeg: bytes) -> str | None:
    """Write one annotated frame per track, overwritten in place.

    One file per track rather than per sample — at 4 Hz a per-sample history
    would fill a disk in minutes, and the keypoint time series in track_store is
    the thing worth keeping. Best-effort: a disk error must never fail the
    analysis that produced it.
    """
    name = f"track_{track_id}.jpg"
    try:
        os.makedirs(TRACK_FRAMES_DIR, exist_ok=True)
        existing = [f for f in os.listdir(TRACK_FRAMES_DIR) if f.endswith(".jpg")]
        if name not in existing and len(existing) >= TRACK_FRAMES_MAX:
            oldest = min(existing, key=lambda f: os.path.getmtime(
                os.path.join(TRACK_FRAMES_DIR, f)))
            os.remove(os.path.join(TRACK_FRAMES_DIR, oldest))
        with open(os.path.join(TRACK_FRAMES_DIR, name), "wb") as fh:
            fh.write(jpeg)
        return name
    except OSError as e:
        log.warning("could not write track frame %s: %s", name, e)
        return None


def create_app(policy: Policy, vlm: VLMBackend, mqtt: MQTTBus, sms: SMSBus,
               static_dir: str, face_id=None, llm: LLMBackend | None = None,
               pose=None) -> Flask:
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
                for on_reply() reasoning; exposed via /health
    pose        optional PoseBackend, used by /track/analyze and exposed via
                /health. None means this hub does no pose estimation, which
                is a clean 'unavailable' rather than an error
    static_dir  directory holding the app's dashboard.html, test_*.html
    """
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

    icons.load_cache()
    icons.start_boot_warming(vlm)
    http_port = int(os.environ.get("QONCLAVE_PORT", "8000"))
    discovery.start_broadcaster(http_port=http_port)
    device_registry.start_rtt_prober()

    # --- /health, / --------------------------------------------------------
    # Every spec route is mounted under /api/v1 (the `servers` base path in
    # spec/v1/openapi/hub.yaml) and every pre-spec route keeps its unprefixed
    # name. The two never collide, so both can be served for as long as the
    # fleet needs — this is a second name for one handler, not a fork.
    @app.get(f"{API_PREFIX}/health")
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
            "pose": pose.status() if pose else {"available": False},
            "sms": sms.status(),
        })

    @app.get("/")
    def root():
        return redirect("/user/dashboard", code=302)

    @app.errorhandler(413)
    def too_large(_e):
        return jsonify({"ok": False,
                        "error": f"upload exceeds {MAX_UPLOAD_MB} MB limit"}), 413

    # --- spec endpoints not implemented in this deployment ------------------
    def _not_implemented(name: str):
        return jsonify({
            "error": "not_implemented",
            "endpoint": f"{API_PREFIX}/{name}",
            "reason": NOT_IMPLEMENTED[name],
            "spec": "spec/v1/openapi/hub.yaml",
        }), 501

    @app.get(f"{API_PREFIX}/capabilities")
    def api_capabilities():
        return _not_implemented("capabilities")

    @app.post(f"{API_PREFIX}/checkin")
    def api_checkin():
        return _not_implemented("checkin")

    @app.post(f"{API_PREFIX}/grants")
    def api_grants():
        return _not_implemented("grants")

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

    @app.post(f"{API_PREFIX}/events")
    @app.post("/edge/event")
    def edge_event():
        """Device contract: ingest frame + event, run policy, record, respond."""
        client = request.remote_addr
        log.info("POST /edge/event from %s (content-type=%s, len=%s)",
                 client, request.headers.get("Content-Type"), request.content_length)

        # One validated model from here down, whichever vocabulary arrived.
        # adapter.to_legacy_dict renders it back for the Policy, which still
        # takes a dict; phase 3 retires that call.
        # One validated model from here down, whichever vocabulary arrived.
        event = transport.parse_edge_event()

        path, err = transport.save_incoming_image(event)
        if err:
            log.warning("POST /edge/event rejected from %s: %s", client, err)
            return jsonify({"received": False, "error": err}), 400

        device_registry.record(device_id=event.source_node_id, ip=client,
                               source="event")

        event_id = event.event_id
        frame_name = os.path.basename(path) if path else None
        log.info("Edge event %s | device=%s | edge_conf=%s | frame=%s",
                 event_id, event.source_node_id, event.confidence,
                 frame_name or "(none)")

        verdict = policy.evaluate(event, path)
        command = policy.command_for(verdict, event)
        device_id = event.source_node_id
        # The Policy returns a spec Command; the wire form carries both that and
        # the flat shape today's firmware parses, so one payload serves both.
        command_wire = adapter.command_to_wire(command) if command is not None else None
        if command_wire is not None and device_id:
            mqtt.publish_command(device_id, command_wire)

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
            "command": command_wire,
            **verdict.extra,
        }

        if verdict.verified:
            log.info("ALERT [%s]: %s", event_id, verdict.alert)
        else:
            log.info("No alert [%s]: %s", event_id, verdict.alert)

        # record for the dashboard (includes reasoning text + edge context)
        record = {
            **response,
            "device_id": event.source_node_id,
            "edge_confidence": event.confidence,
            "edge_model": (event.metadata or {}).get("edge_model"),
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
        # No frame means no sidecar — it is named after the frame it sits beside.
        if frame_name:
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

    # --- /track/analyze: unified per-track analysis -------------------------
    @app.post("/track/analyze")
    def track_analyze():
        """Device contract: analyse ONE cropped person, tagged with the edge's
        own track_id, with whichever analyzers the caller asks for.

        Replaces POST /recognize. One crop, one request, fanned out hub-side to
        every analyzer that wants it — rather than a second endpoint with its
        own sampling loop. A known person then costs one request per pose tick
        instead of two.

        Request:  multipart 'image' (or raw image body)
                  + 'track_id'  (form / query / JSON)
                  + 'analyzers' (comma-separated, default "face,pose")
                  + 'person_box' (optional "x1,y1,x2,y2" inside the crop)
        Response: {"track_id", "face": {...}, "pose": {...}, "latency_ms": {...}}

        Each analyzer contributes an independent sub-object, so one being
        unavailable never fails the other. The uploaded crop is deleted in a
        finally, exactly as /recognize did.
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

        requested = (request.form.get("analyzers") or request.args.get("analyzers")
                     or "face,pose")
        analyzers = {a.strip().lower() for a in requested.split(",") if a.strip()}

        person_box = _parse_person_box(
            request.form.get("person_box") or request.args.get("person_box"))

        # No device id on this endpoint — the crop is tagged with a track_id,
        # not a node id — so the sighting is anonymous until an /edge/event
        # from the same IP names it.
        device_registry.record(ip=client, source="track")

        path, err = transport.save_incoming_image()
        # `not path` is separate from `err`: since payload-free events became
        # legal, save_incoming_image returns (None, None) when nothing was
        # offered at all. That is fine for an event — a threshold crossing has
        # nothing to look at — but this endpoint exists to analyse a crop, so
        # here it is simply a bad request.
        if err or not path:
            reason = err or "no image supplied; /track/analyze requires a crop"
            log.warning("POST /track/analyze rejected from %s (track_id=%s): %s",
                        client, track_id, reason)
            return jsonify({"ok": False, "error": reason}), 400

        response = {"track_id": track_id}
        latency_ms = {}
        image_bytes = b""

        try:
            with open(path, "rb") as f:
                image_bytes = f.read()

            if "face" in analyzers:
                t0 = time.monotonic()
                response["face"] = _run_face(face_id, path)
                latency_ms["face"] = round((time.monotonic() - t0) * 1000, 1)

            if "pose" in analyzers:
                t0 = time.monotonic()
                response["pose"] = _run_pose(pose, path, person_box)
                latency_ms["pose"] = round((time.monotonic() - t0) * 1000, 1)
        finally:
            # The crop is transient by design — unlike an escalation frame, it is
            # a sampled body crop and the privacy cascade says it should not
            # persist. The annotated copy below is opt-in and capped.
            try:
                os.remove(path)
            except OSError:
                pass

        response["latency_ms"] = latency_ms

        face_result = response.get("face") or {}
        pose_result = response.get("pose") or {}

        frame_name = None
        annotated = image_bytes
        if pose_result.get("status") == "ok" and pose_result.get("keypoints"):
            label = face_result.get("identity") if face_result.get("status") == "known" else None
            annotated = pose_overlay.draw_pose_overlay(
                image_bytes, pose_result["keypoints"], label)
        if TRACK_FRAMES_ENABLED:
            frame_name = _save_track_frame(track_id, annotated)

        track_store.record(track_id, face_result, pose_result, frame_name)

        log.info("POST /track/analyze track_id=%s | face=%s | pose=%s | %s from %s",
                 track_id, face_result.get("status", "-"),
                 pose_result.get("status", "-"), latency_ms, client)

        # Keep the legacy activity buffer fed so the existing dashboard panel
        # keeps working while its replacement (/user/tracks) beds in.
        if "face" in analyzers:
            recognize_activity.record(
                track_id, face_result.get("identity", "unavailable"),
                float(face_result.get("confidence") or 0.0),
                face_result.get("status", "unavailable"),
                latency_ms.get("face", 0.0), image_bytes, source_ip=client,
            )

        return jsonify(response)

    @app.get("/user/devices")
    def user_devices():
        """Devices seen on the network, most recent first. Subscribing to the
        status topics here (idempotent, like /test/mqtt/messages) means MQTT
        sightings start flowing once a broker is up, however late that is."""
        mqtt.subscribe("qonclave/status/+")
        mqtt.subscribe("qonclave/+/status")
        devices = device_registry.snapshot()
        return jsonify({
            "hub": {
                "app": policy.name,
                "hostname": discovery.MDNS_NAME,
                "ip": discovery.lan_ip(),
                "port": http_port,
                "time": transport.now_iso(),
            },
            "count": len(devices),
            "devices": devices,
        })

    @app.get("/user/network")
    def user_network():
        return send_from_directory(static_dir, "network.html")

    @app.get("/user/tracks")
    def user_tracks():
        """Live per-track state: identity, latest pose, history depth."""
        return jsonify({"tracks": track_store.snapshot()})

    @app.get("/user/tracks/<int:track_id>.jpg")
    def user_track_frame(track_id):
        """The latest annotated frame for one track."""
        name = track_store.latest_frame(track_id)
        if not name:
            return jsonify({"error": "no frame for that track"}), 404
        return send_from_directory(TRACK_FRAMES_DIR, name)

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

        command = policy.on_reply(sender, body)
        if command is not None:
            device_id = events.latest_device_id()
            if device_id:
                wire = adapter.command_to_wire(command)
                mqtt.publish_command(device_id, wire)
                log.info("SMS reply MQTT command %s -> device %s", wire, device_id)
                action = "mqtt_published"
            else:
                log.warning("SMS reply returned command %s but no device_id known yet", command)
                action = "ignored"
        elif body.strip().upper() == "STOP":
            action = "suppressed"
        else:
            action = "ignored"

        reply_text = policy.reply_for(sender, body)
        if reply_text:
            from .policy import Notification
            sent = sms.send(Notification(message=reply_text, recipient=sender))
            log.info("SMS reply_for -> sent=%s: %r", sent, reply_text[:80])

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
        """Recent per-track face results (track_id, identity, confidence,
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
        """App-specific dashboard state — for this app, the latest LLM analysis
        of an inbound SMS reply. The framework serves whatever the Policy
        returns without interpreting it."""
        analysis = policy.dashboard_state()
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
