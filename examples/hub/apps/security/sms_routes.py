"""
sms_routes.py — Flask blueprint for the security app's Twilio SMS webhook.

Moved out of hub/framework/server.py: both routes read Twilio's own wire
shapes (POST /sms's From/Body form fields, /user/sms_activity's suppressed/
activity fields from the app's own SMSBus) — that's app-specific, not
generic framework HTTP surface. Registered from hub/server.py directly
rather than through create_app(), so framework/server.py never imports
anything from apps/ for this (see CONVENTIONS.md's note on the
apps.assistant.routes import it still does, pending Phase 8).

Endpoints:
    POST /sms                 Twilio inbound-reply webhook: runs
                               policy.on_reply(), optionally publishes an
                               MQTT command, replies via policy.reply_for()
    GET  /user/sms_activity    recent SMS activity (outbound + inbound), JSON
    GET/POST /user/sms-settings  read/toggle the dashboard's outbound-SMS switch
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from framework import adapter, events
from framework.mqtt_bus import MQTTBus
from framework.policy import Notification, Policy

from .egress.twilio_sms import SMSBus

log = logging.getLogger("qonclave.hub")


def create_sms_blueprint(policy: Policy, mqtt: MQTTBus, sms: SMSBus) -> Blueprint:
    bp = Blueprint("security_sms", __name__)

    @bp.post("/sms")
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
                command_wire = adapter.command_to_wire(command)
                mqtt.publish_command(device_id, command_wire)
                log.info("SMS reply MQTT command %s -> device %s", command.action, device_id)
                action = "mqtt_published"
            else:
                log.warning("SMS reply returned command %s but no device_id known yet", command.action)
                action = "ignored"
        elif body.strip().upper() == "STOP":
            action = "suppressed"
        else:
            action = "ignored"

        reply_text = policy.reply_for(sender, body)
        if reply_text:
            sent = sms.send(Notification(message=reply_text, recipient=sender))
            log.info("SMS reply_for -> sent=%s: %r", sent, reply_text[:80])

        sms.record_reply(sender, body, action)
        return ("", 200)

    @bp.get("/user/sms_activity")
    def user_sms_activity():
        """Recent SMS activity (outbound + inbound), newest first."""
        limit = request.args.get("limit", type=int) or 50
        return jsonify({
            "count": len(sms.recent_activity(limit)),
            "suppressed": sms._suppressed,
            "activity": sms.recent_activity(limit),
        })

    @bp.route("/user/sms-settings", methods=["GET", "POST"])
    def user_sms_settings():
        """Dashboard toggle for enabling/disabling outbound SMS."""
        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            if "disabled" not in body:
                return jsonify({"ok": False, "error": "missing 'disabled' field"}), 400
            sms.set_user_disabled(bool(body["disabled"]))
        return jsonify({"ok": True, "disabled": sms._user_disabled})

    return bp
