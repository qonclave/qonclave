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
    POST /track/analyze       per-track-id analysis: a single cropped-person
                               JPEG + track_id, fanned out to the requested
                               analyzers (face identification, pose estimation)
    POST /edge/investigation  edge's answer to a capture_investigation_image
                               MQTT command: event_id + one fresh frame,
                               handed to the policy's investigation flow
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
    GET  /user/known-person-priorities         enrolled people + follow
                               priority (404 unless the policy provides the
                               known_person_priorities hook)
    PUT  /user/known-person-priorities/<slug>  set one person's priority
                               (JSON body {"priority": <positive int>})
    GET  /user/recognize_activity        recent face-analyzer calls (JSON)
    GET  /user/recognize_activity/<id>.jpg  the crop for one of those calls
    GET  /user/tracks         per-track identity + latest pose + history length
    GET  /user/tracks/<id>.jpg  latest skeleton-annotated frame for a track
    GET  /user/investigation  current investigation state machine snapshot
    POST /user/investigate    dashboard trigger: fresh capture + one VLM check

    POST /assistant/query      edge voice assistant: transcribed command ->
                               LLM (or canned template) reply
    GET  /user/assistant_activity  LLM status + recent assistant exchanges,
                               for the dashboard's assistant card
    (both live in apps/assistant/routes.py, registered below)

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

from flask import Flask, Response, jsonify, redirect, request, send_from_directory
from qonclave.placement import InferenceTask, PlacementPolicy, resolve
from qonclave.placement.probe import build, local_state
from qonclave.placement.tiers import Tier

from . import adapter, device_registry, discovery, events, icons, recognize_activity, track_store, transport
from .face_id.identity import _slugify_name
from .llm import LLMBackend
from .mqtt_bus import MQTTBus
from .policy import Policy
from .sms_bus import SMSBus
from .vlm import VLMBackend

# "apps" is a sibling top-level package (hub/ is on sys.path, it is not itself a
# package), so this must be absolute — same style as hub/server.py's
# "from apps.security.policy import SecurityPolicy".
from apps.assistant.routes import create_assistant_blueprint

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

# Per-track annotated pose frames.
#
# Two independent switches, because watching and keeping are different things:
#   QONCLAVE_TRACK_STREAM_ENABLED  draw the skeleton overlay and hold the
#       latest frame in memory, so /user/tracks/<id>/stream.mjpg can serve
#       live pose video. Nothing touches disk.
#   QONCLAVE_TRACK_FRAMES_ENABLED  additionally persist it as
#       track_frames/track_<id>.jpg. Retention note: this is imagery living
#       on the hub, which the privacy cascade otherwise avoids — capped and
#       gitignored, but set it to 0 for any non-demo deployment. The live
#       stream keeps working with it off.
TRACK_FRAMES_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "track_frames"))
TRACK_FRAMES_ENABLED = os.environ.get("QONCLAVE_TRACK_FRAMES_ENABLED", "1") == "1"
TRACK_FRAMES_MAX = int(os.environ.get("QONCLAVE_TRACK_FRAMES_MAX", "50"))
TRACK_STREAM_ENABLED = os.environ.get("QONCLAVE_TRACK_STREAM_ENABLED", "1") == "1"
# How long a stream waits on a silent track before closing. The edge samples
# pose at ~4 Hz, so several seconds of silence means the track is gone.
TRACK_STREAM_IDLE_TIMEOUT = float(os.environ.get("QONCLAVE_TRACK_STREAM_IDLE_SEC", "20"))


def _publish_track_frame(track_id: int, crop_jpeg: bytes, keypoints, label: str) -> "str | None":
    """Draw the skeleton overlay, publish it to the live stream, and (when
    retention is on) write/overwrite track_<id>.jpg — one file per track,
    always the latest sample, the edge's save_crop_locally convention.
    Prunes oldest files beyond TRACK_FRAMES_MAX. Best-effort: returns the
    on-disk filename or None, never raises."""
    try:
        from .pose.overlay import draw_pose_overlay

        annotated = draw_pose_overlay(crop_jpeg, keypoints, label)
        if annotated is None:
            return None

        if TRACK_STREAM_ENABLED:
            track_store.record_frame(track_id, annotated)

        if not TRACK_FRAMES_ENABLED:
            return None

        os.makedirs(TRACK_FRAMES_DIR, exist_ok=True)
        name = f"track_{track_id}.jpg"
        with open(os.path.join(TRACK_FRAMES_DIR, name), "wb") as f:
            f.write(annotated)

        # Cap total files: prune oldest-written beyond the limit so a long
        # session with many short-lived tracks can't fill the disk.
        entries = sorted(
            (e for e in os.scandir(TRACK_FRAMES_DIR) if e.is_file()),
            key=lambda e: e.stat().st_mtime,
        )
        for stale in entries[:-TRACK_FRAMES_MAX] if len(entries) > TRACK_FRAMES_MAX else []:
            try:
                os.remove(stale.path)
            except OSError:
                pass
        return name
    except Exception:
        log.exception("failed to publish annotated track frame for track %s", track_id)
        return None


def create_app(policy: Policy, vlm: VLMBackend, mqtt: MQTTBus, sms: SMSBus,
               static_dir: str, face_id=None, llm: LLMBackend | None = None,
               pose=None, placement: PlacementPolicy | None = None,
               assistant_llm: LLMBackend | None = None) -> Flask:
    """
    Build the Qonclave hub Flask app for one Policy.

    policy      the app's Policy instance (evaluate/command_for/notify_for)
    vlm         shared VLMBackend, exposed via /health and /user/reason
    mqtt        shared MQTTBus; commands from command_for() are also
                published here so a device can receive them without an
                open HTTP request
    face_id     optional FaceIdentityBackend, exposed via /health and used by
                /track/analyze's face analyzer
    pose        optional PoseBackend, exposed via /health and used by
                /track/analyze's pose analyzer
    sms         shared SMSBus; sends an SMS when notify_for() returns a
                Notification (trial mode: fixed template + fixed number)
    llm         optional LLMBackend (text-only Qwen3-4B); used by the Policy
                for on_reply() reasoning; exposed via /health
    placement   optional qonclave.placement.PlacementPolicy instance. When
                given, /edge/event runs it (observability only today -- this
                deployment has no compute tier, so the resolved tier is
                always where the event was already going) and logs the
                resolution. The app supplies its own PlacementPolicy the same
                way it supplies its own Policy; framework/ never imports one.
    assistant_llm
                LLMBackend the /assistant/query route generates with, or None
                to make it serve canned template replies instead. Separate
                from llm so the assistant can be switched off (see
                ASSISTANT_LLM_ENABLED in hub/server.py) without disabling
                /health reporting or the Policy's own LLM use.
    static_dir  directory holding the app's dashboard.html, test_*.html
    """
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

    icons.load_cache()
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

        # One validated model from here down, all the way through the Policy.
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

        if placement is not None:
            try:
                task = InferenceTask.from_event(event, task_id=event_id)
                tiers = build(local_state("hub", Tier.HUB))
                resolution = resolve(task, tiers, placement)
                log.debug("Placement for %s: %s", event_id, resolution.explain())
            except Exception as e:
                # Observability only -- today's single-laptop deployment has
                # nowhere else to send the event anyway, so a placement bug
                # must never block ingestion.
                log.warning("Placement decision failed for %s: %s", event_id, e)

        verdict = policy.evaluate(event, image_path=path)
        command = policy.command_for(verdict, event)
        command_wire = adapter.command_to_wire(command) if command is not None else None
        device_id = event.source_node_id
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
        """Device contract: retrieve or render the Level 2 cached 12x8 icon
        bitmap. Rendered locally and deterministically -- no VLM, so an icon
        request can never delay a posture investigation. POST is still
        accepted for compatibility, but any uploaded frame is ignored: it only
        ever existed as visual context for the removed VLM prompt."""
        label = request.args.get("label", "clear").lower().strip()
        client = request.remote_addr
        log.info("%s /edge/icon?label=%s from %s", request.method, label, client)

        entry = icons.get_or_generate_icon(label)
        return jsonify({
            "ok": True,
            "label": label,
            "bitmap": entry.get("bitmap"),
            "updated_at": entry.get("updated_at"),
            "permanent": entry.get("permanent", False)
        })

    @app.post("/edge/investigation")
    def edge_investigation():
        """Device contract: deliver the investigation image requested by a
        capture_investigation_image MQTT command. Multipart 'image' (or raw
        image body) + 'event_id'; optional 'device_id'. The policy decides
        whether the event is still waiting for it."""
        client = request.remote_addr
        handler = getattr(policy, "on_investigation_capture", None)
        if handler is None:
            return jsonify({"ok": False,
                            "error": "app has no investigation flow"}), 501

        event_id = (request.form.get("event_id") or request.args.get("event_id")
                    or "").strip()
        if not event_id:
            return jsonify({"ok": False, "error": "missing 'event_id'"}), 400
        events.note_device(request.form.get("device_id")
                           or request.args.get("device_id"))

        path, err = transport.save_incoming_image()
        # `not path` is separate from `err`: since payload-free events became
        # legal, save_incoming_image can return (None, None) when nothing was
        # offered at all. That is fine for an event with no frame, but this
        # endpoint exists to deliver one, so here it is simply a bad request.
        if err or not path:
            reason = err or "no image supplied; /edge/investigation requires a frame"
            log.warning("POST /edge/investigation rejected from %s (%s): %s",
                        client, event_id, reason)
            return jsonify({"ok": False, "error": reason}), 400
        try:
            with open(path, "rb") as f:
                image_bytes = f.read()
        finally:
            # The staging upload is short-lived; the investigation flow saves
            # its own composite under a stable name for the dashboard.
            try:
                os.remove(path)
            except OSError:
                pass

        log.info("POST /edge/investigation %s from %s (%d bytes)",
                 event_id, client, len(image_bytes))
        result = handler(event_id, image_bytes)
        status = 200 if result.get("ok") else 409
        return jsonify(result), status

    # --- /track/analyze: per-track-id analysis (face + pose) ----------------
    @app.post("/track/analyze")
    def track_analyze():
        """Device contract: run the requested analyzers on the single cropped
        person in the uploaded image, tagged with the edge's own track_id.
        One crop, one request — the hub fans it out to every analyzer asked
        for, and one analyzer being unavailable never fails the other.

        Request:  multipart 'image' file (or raw image body) + 'track_id'
                   (form field, query param, or JSON field).
                  Optional 'analyzers': comma-separated subset of
                   "face,pose" (default both).
                  Optional 'person_box': "x1,y1,x2,y2" — the unpadded person
                   rect in the crop's own pixels. The crop is framed for face
                   detection (large headroom); pose re-frames around this
                   tight box. Absent/invalid -> pose uses the whole crop.
        Response: {"track_id": int,
                   "face": {"identity": str, "confidence": float,
                            "status": "known"|"unknown"|"no_face"|"unavailable"},
                   "pose": {"status": "ok"|"no_pose"|"unavailable",
                            "keypoints": [[x,y,score]x17]|None,
                            "mean_score": float|None},
                   "latency_ms": {"face": float, "pose": float}}
                  Only requested analyzers appear as sub-objects.
        The crop is deleted from disk right after inference, as /recognize
        always did — short-lived copies live in memory (recognize_activity's
        capped ring buffer) and, when QONCLAVE_TRACK_FRAMES_ENABLED=1, as the
        single per-track annotated frame in track_frames/.
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

        # With periodic /edge/event escalation off, these samples are how the
        # hub learns which device to target with MQTT commands.
        track_device_id = (request.form.get("device_id")
                           or request.args.get("device_id"))
        events.note_device(track_device_id)

        # device_id is optional here (the crop is tagged with a track_id, not
        # a device id, so an older/minimal device may omit it) — pass it
        # through when present so this sighting merges into the same row as
        # this device's other announcements instead of sitting anonymous
        # under "ip:<addr>" forever.
        device_registry.record(device_id=track_device_id, ip=client, source="track")

        raw_analyzers = request.form.get("analyzers") or request.args.get("analyzers") \
            or "face,pose"
        analyzers = {a.strip() for a in raw_analyzers.split(",") if a.strip()}
        unknown = analyzers - {"face", "pose"}
        if unknown or not analyzers:
            return jsonify({"ok": False,
                            "error": f"unknown analyzers: {sorted(unknown) or 'none requested'}"}), 400

        # Malformed person_box degrades to whole-crop pose, never a 400 — a
        # framing hint must not cost the sample.
        person_box = None
        raw_box = request.form.get("person_box") or request.args.get("person_box")
        if raw_box:
            try:
                parts = [float(v) for v in raw_box.split(",")]
                if len(parts) == 4:
                    person_box = tuple(parts)
            except ValueError:
                pass

        # A known edge track may stop requesting face inference. It echoes
        # the identity the hub previously returned so a hub restart can
        # recover that association. Accept only names still enrolled here.
        carried_face = None
        raw_known_identity = (request.form.get("known_identity")
                              or request.args.get("known_identity"))
        if raw_known_identity and face_id is not None and "face" not in analyzers:
            known_names = getattr(face_id, "known_names", lambda: [])()
            enrolled = next((name for name in known_names
                             if name.casefold() == raw_known_identity.strip().casefold()), None)
            if enrolled:
                carried_face = {"identity": enrolled, "status": "known"}

        path, err = transport.save_incoming_image()
        # `not path` is separate from `err`: since payload-free events became
        # legal, save_incoming_image can return (None, None) when nothing was
        # offered at all. That is fine for an event with no frame, but this
        # endpoint exists to analyse a crop, so here it is simply a bad request.
        if err or not path:
            reason = err or "no image supplied; /track/analyze requires a crop"
            log.warning("POST /track/analyze rejected from %s (track_id=%s): %s",
                        client, track_id, reason)
            return jsonify({"ok": False, "error": reason}), 400

        face_result = None
        pose_result = None
        latency_ms: dict = {}
        try:
            with open(path, "rb") as f:
                image_bytes = f.read()

            if "face" in analyzers:
                t0 = time.monotonic()
                if face_id is None:
                    result = {"available": False}
                else:
                    result = face_id.identify(path)
                latency_ms["face"] = round((time.monotonic() - t0) * 1000, 1)

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
                face_result = {"identity": identity,
                               "confidence": round(float(confidence), 4),
                               "status": status}

            if "pose" in analyzers:
                t0 = time.monotonic()
                if pose is None:
                    result = {"available": False, "status": "unavailable",
                              "keypoints": None, "mean_score": None}
                else:
                    result = pose.estimate(path, person_box=person_box)
                latency_ms["pose"] = round((time.monotonic() - t0) * 1000, 1)
                pose_result = {"status": result.get("status", "unavailable"),
                               "keypoints": result.get("keypoints"),
                               "mean_score": result.get("mean_score")}
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

        log.info("POST /track/analyze track_id=%s [%s] ->%s%s (%s) from %s",
                 track_id, ",".join(sorted(analyzers)),
                 f" face={face_result['status']}"
                 + (f" ({face_result['identity']})" if face_result and face_result["status"] == "known" else "")
                 if face_result else "",
                 f" pose={pose_result['status']}" if pose_result else "",
                 " ".join(f"{k}={v:.0f}ms" for k, v in latency_ms.items()), client)

        # The dashboard's live recognition feed predates /track/analyze and
        # stays fed by the face analyzer's results.
        if face_result is not None:
            recognize_activity.record(
                track_id, face_result["identity"], face_result["confidence"],
                face_result["status"], latency_ms.get("face", 0.0),
                image_bytes, source_ip=client,
            )

        frame_name = None
        if ((TRACK_FRAMES_ENABLED or TRACK_STREAM_ENABLED)
                and pose_result is not None and pose_result["status"] == "ok"):
            label = f"Track {track_id}"
            effective_face = face_result or carried_face
            identity = effective_face["identity"] if (
                effective_face and effective_face["status"] == "known") else None
            if identity is None:
                for sample in reversed(track_store.history(track_id)):
                    if sample.get("status") == "known":
                        identity = sample.get("identity")
                        break
            if identity:
                label += f": {identity}"
            frame_name = _publish_track_frame(
                track_id, image_bytes, pose_result["keypoints"], label)

        # Face sampling normally stops after a track resolves. Give app-level
        # analysis the retained result so pose-only ticks keep their identity.
        analysis_face = face_result or carried_face
        if analysis_face is None:
            for sample in reversed(track_store.history(track_id)):
                if sample.get("status"):
                    analysis_face = {"identity": sample.get("identity"),
                                     "status": sample.get("status")}
                    break
        analyze_track = getattr(policy, "analyze_track", None)
        analysis = (analyze_track(track_id, image_bytes, analysis_face, pose_result)
                    if analyze_track else None)
        track_store.record(track_id, face_result or carried_face, pose_result,
                           frame_name, analysis)

        response = {"track_id": track_id, "latency_ms": latency_ms}
        if face_result is not None:
            response["face"] = face_result
        if pose_result is not None:
            response["pose"] = pose_result
        if analysis is not None:
            response["analysis"] = analysis
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

    @app.get("/user/tracks")
    def user_tracks():
        """Per-track latest identity + pose + retained history length — the
        keypoint ring buffer /track/analyze feeds (see track_store.py)."""
        tracks = track_store.snapshot()
        return jsonify({"count": len(tracks), "tracks": tracks})

    @app.route("/user/track-settings", methods=["GET", "POST"])
    def user_track_settings():
        """Expose optional app-owned, UI-tunable tracking thresholds."""
        if request.method == "GET":
            settings = policy.track_settings()
        else:
            try:
                settings = policy.update_track_settings(request.get_json(silent=True) or {})
            except (TypeError, ValueError) as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400
        if settings is None:
            return jsonify({"ok": False, "error": "track settings unsupported"}), 404
        return jsonify({"ok": True, "settings": settings})

    @app.get("/user/investigation")
    def user_investigation():
        """Current investigation state (MONITORING/WAITING_FOR_CAPTURE/
        VLM_RUNNING/COOLDOWN), the active event, and the last result."""
        status_fn = getattr(policy, "investigation_status", None)
        if status_fn is None:
            return jsonify({"available": False}), 404
        return jsonify({"available": True, **status_fn()})

    @app.post("/user/investigate")
    def user_investigate():
        """Dashboard trigger: request a fresh edge capture and one VLM check.
        409 when an investigation is already mid-flight."""
        trigger = getattr(policy, "trigger_investigation", None)
        if trigger is None:
            return jsonify({"ok": False,
                            "error": "app has no investigation flow"}), 501
        result = trigger()
        log.info("POST /user/investigate from %s -> %s",
                 request.remote_addr, result)
        return jsonify(result), 200 if result.get("ok") else 409

    @app.get("/user/tracks/<int:track_id>.jpg")
    def user_track_frame(track_id):
        """Latest skeleton-annotated frame for one track — a single still.
        Served from memory when the live stream is enabled, else from the
        retained file. 404 until the track has produced an ok pose."""
        image = track_store.latest_frame_bytes(track_id)
        if image is not None:
            return Response(image, mimetype="image/jpeg")
        name = track_store.latest_frame(track_id)
        if name is None or not os.path.exists(os.path.join(TRACK_FRAMES_DIR, name)):
            return jsonify({"error": "no annotated frame for this track"}), 404
        return send_from_directory(TRACK_FRAMES_DIR, name, mimetype="image/jpeg")

    @app.get("/user/tracks/<int:track_id>/stream.mjpg")
    def user_track_stream(track_id):
        """Live pose video for one track, as multipart MJPEG — drop it
        straight into an <img src>, no JavaScript required.

        Frame rate is whatever the edge samples pose at (POSE_SAMPLE_INTERVAL_SEC,
        default 4 Hz), not a fixed video rate: each /track/analyze result with
        an ok pose publishes one frame. The stream closes itself once a track
        goes silent for TRACK_STREAM_IDLE_TIMEOUT, so a browser tab left open
        on a departed person doesn't hold a worker thread forever.
        """
        if not TRACK_STREAM_ENABLED:
            return jsonify({"error": "track streaming disabled "
                                     "(QONCLAVE_TRACK_STREAM_ENABLED=0)"}), 404

        def generate():
            last_seq = -1
            idle = 0.0
            while idle < TRACK_STREAM_IDLE_TIMEOUT:
                frame, seq = track_store.wait_for_frame(track_id, last_seq, timeout=2.0)
                if frame is None:
                    idle += 2.0
                    continue
                idle = 0.0
                last_seq = seq
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n"
                       b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
                       + frame + b"\r\n")

        return Response(generate(),
                        mimetype="multipart/x-mixed-replace; boundary=frame")

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
            return jsonify({"ok": False, "error": "duration must be >= 0 ms"}), 400
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
        inference run will match against the newly enrolled person.

        Optional 'additional' field (1/true/yes/on): keep this person's
        existing photos and add this one as another angle, instead of
        replacing them. Recognition scores the best match across a person's
        photos, so an extra angle can only help."""
        client = request.remote_addr
        if face_id is None:
            return jsonify({"ok": False, "error": "face ID not enabled on this hub"}), 501

        name = (request.form.get("name") or request.args.get("name") or "").strip()
        if not name:
            return jsonify({"ok": False, "error": "missing 'name'"}), 400

        raw_additional = (request.form.get("additional")
                          or request.args.get("additional") or "")
        additional = raw_additional.strip().lower() in ("1", "true", "yes", "on")

        path, err = transport.save_incoming_image()
        if err:
            log.warning("POST /user/known_faces rejected from %s: %s", client, err)
            return jsonify({"ok": False, "error": err}), 400

        result = face_id.enroll(name, path, additional=additional)
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

    # --- /user/known-person-priorities: follow priorities for enrolled ------
    # people. App-agnostic: the policy opts in by providing the hooks
    # (precedent: /user/investigation); 404 when it doesn't.
    @app.get("/user/known-person-priorities")
    def list_known_person_priorities():
        fn = getattr(policy, "known_person_priorities", None)
        if fn is None:
            return jsonify({"error": "app has no known-person priorities"}), 404
        return jsonify({"people": fn()})

    @app.put("/user/known-person-priorities/<slug>")
    def update_known_person_priority(slug):
        fn = getattr(policy, "update_known_person_priority", None)
        if fn is None:
            return jsonify({"ok": False,
                            "error": "app has no known-person priorities"}), 404
        # Same normalization as enrollment, so the path param always matches
        # the stored slug — and traversal characters collapse to '_'.
        slug = _slugify_name(slug)
        body = request.get_json(silent=True) or {}
        try:
            result = fn(slug, body.get("priority"))
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        if result is None:
            return jsonify({"ok": False, "error": "person not enrolled"}), 404
        log.info("PUT /user/known-person-priorities/%s -> %s from %s",
                 slug, result["priority"], request.remote_addr)
        return jsonify({"ok": True, **result})

    # --- app pages (served from the app's own static_dir) -------------------
    @app.get("/user/dashboard")
    def user_dashboard():
        return send_from_directory(static_dir, "dashboard.html")

    @app.get("/user/")
    @app.get("/user")
    def user_index():
        # default landing = dashboard
        return send_from_directory(static_dir, "dashboard.html")

    app.register_blueprint(create_assistant_blueprint(assistant_llm))

    return app
