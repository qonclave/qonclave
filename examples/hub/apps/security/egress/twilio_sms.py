"""
twilio_sms.py — Twilio-backed SMS egress for the security app.

Trial-mode: the API accepts a Notification(message, recipient) from the
caller but always sends a fixed template message to a fixed phone number
via Twilio. The message/recipient arguments are accepted now and will be
wired through in a future release.

Public API:
    bus = SMSBus()              # cheap; does not import twilio
    bus.is_available()          # True if enabled + credentials present
    bus.send(notification)      # -> bool; never raises for the caller
    bus.status()                # for /health

Lazy import, best-effort, never raises for the caller. If credentials are
missing or the Twilio call fails, the hub keeps serving HTTP/dashboard
traffic and send() is a logged no-op.

Environment:
    QONCLAVE_SMS_ENABLED        1 (default) to enable; 0 to disable
    TWILIO_ACCOUNT_SID          Twilio account SID
    TWILIO_AUTH_TOKEN           Twilio auth token
    TWILIO_FROM_NUMBER          Twilio-provisioned sending number, E.164
    TWILIO_TO_NUMBER            trial-mode fixed recipient, E.164
    QONCLAVE_SMS_MIN_RESEND_SEC minimum seconds between sends (default 120);
                                stops the investigation re-check loop (which
                                re-fires every cooldown_seconds while someone
                                stays down) from texting the same alert again
                                and again with just reworded VLM text

This is entirely app-owned, by design: which vendor, which credentials, and
what "activity" means are all specific to this use case, not core framework
concerns. qonclave.hub.egress.sms stays a placeholder rather than a generic
transport contract for exactly that reason -- see CONVENTIONS.md.
"""

from __future__ import annotations

import collections
import datetime as _dt
import logging
import os
import threading
import time

from qonclave.hub.policy import Notification

log = logging.getLogger("qonclave.sms")

_TEMPLATE_BODY = "sms_appointment_reminders"


class SMSBus:
    """Lazily loads the Twilio client. Safe to construct on any machine."""

    def __init__(self):
        self.enabled = os.environ.get("QONCLAVE_SMS_ENABLED", "1") == "1"
        self._account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
        self._auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
        self._from_number = os.environ.get("TWILIO_FROM_NUMBER")
        self._to_number = os.environ.get("TWILIO_TO_NUMBER")
        self._client = None
        self._load_error: str | None = None
        self._load_attempted = False
        self._suppressed = False
        self._user_disabled = True
        self._min_resend_interval = float(
            os.environ.get("QONCLAVE_SMS_MIN_RESEND_SEC", "40"))
        self._last_sent_monotonic: float | None = None
        self._lock = threading.Lock()
        self._activity: collections.deque = collections.deque(maxlen=50)

    # --- capability probe ---------------------------------------------------

    def is_available(self) -> bool:
        if not self.enabled:
            return False
        if self._client is not None:
            return True
        if self._load_attempted:
            return False
        return self._try_load()

    def status(self) -> dict:
        return {
            "available": self._client is not None,
            "enabled": self.enabled,
            "suppressed": self._suppressed,
            "user_disabled": self._user_disabled,
            "load_attempted": self._load_attempted,
            "load_error": self._load_error,
        }

    # --- internal -----------------------------------------------------------

    def _try_load(self) -> bool:
        with self._lock:
            if self._client is not None:
                return True
            if self._load_attempted:
                return False
            self._load_attempted = True

            if not self.enabled:
                self._load_error = "SMS disabled (QONCLAVE_SMS_ENABLED=0)"
                log.info("SMS disabled by config")
                return False

            if not self._account_sid or not self._auth_token:
                self._load_error = (
                    "TWILIO_ACCOUNT_SID and/or TWILIO_AUTH_TOKEN not set"
                )
                log.warning("SMS unavailable: %s", self._load_error)
                return False

            if not self._from_number or not self._to_number:
                self._load_error = (
                    "TWILIO_FROM_NUMBER and/or TWILIO_TO_NUMBER not set"
                )
                log.warning("SMS unavailable: %s", self._load_error)
                return False

            try:
                from twilio.rest import Client  # type: ignore
            except Exception as e:
                self._load_error = f"could not import twilio: {e}"
                log.warning("SMS unavailable: %s", self._load_error)
                return False

            try:
                self._client = Client(self._account_sid, self._auth_token)
                log.info("Twilio client initialised (from=%s, to=%s)",
                         self._from_number, self._to_number)
                return True
            except Exception as e:
                self._load_error = f"Twilio client init failed: {e}"
                log.warning("SMS unavailable: %s", self._load_error)
                self._client = None
                return False

    # --- suppress (STOP reply) -----------------------------------------------

    def suppress(self) -> None:
        """
        Mute all further outbound SMS for this server session. Called by a
        Policy when the recipient replies STOP. Resets on server restart.
        """
        self._suppressed = True
        log.info("SMS suppressed for this session (STOP received)")

    # --- user toggle (dashboard) ---------------------------------------------

    def set_user_disabled(self, disabled: bool) -> None:
        """
        Enable/disable outbound SMS from the dashboard toggle. Independent of
        the STOP-triggered suppress() and the QONCLAVE_SMS_ENABLED env var.
        Resets on server restart.
        """
        self._user_disabled = bool(disabled)
        log.info("SMS %s from dashboard", "disabled" if self._user_disabled else "enabled")

    # --- activity tracking ---------------------------------------------------

    def record_sent(self, content: str, ok: bool) -> None:
        """Record an outbound SMS attempt (called internally by send())."""
        self._activity.appendleft({
            "direction": "out",
            "content": content,
            "status": "sent" if ok else "failed",
            "time": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        })

    def record_reply(self, sender: str, body: str, action: str) -> None:
        """Record an inbound SMS reply (called by the /sms webhook handler)."""
        self._activity.appendleft({
            "direction": "in",
            "content": body,
            "status": action,
            "from": sender,
            "time": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        })

    def recent_activity(self, limit: int = 50) -> list:
        """Recent SMS activity (outbound + inbound), newest first."""
        return list(self._activity)[:limit]

    # --- send ---------------------------------------------------------------

    def send(self, notification: Notification) -> bool:
        """
        Send an SMS notification. In trial mode the notification's message
        and recipient are accepted but not used — the fixed template and
        TO_NUMBER are sent instead.

        Returns True if the message was accepted by Twilio; False on any
        failure (logged). Never raises for the caller.
        """
        if self._suppressed:
            log.warning(
                "SMS suppressed (STOP was received). Skipping message: %r to %s",
                notification.message, notification.recipient,
            )
            return False

        if self._user_disabled:
            log.info(
                "SMS disabled from dashboard. Skipping message: %r to %s",
                notification.message, notification.recipient,
            )
            return False

        now = time.monotonic()
        if (self._last_sent_monotonic is not None
                and now - self._last_sent_monotonic < self._min_resend_interval):
            log.info(
                "SMS resend throttled (last sent %.1fs ago, min interval %.1fs). "
                "Skipping message: %r to %s",
                now - self._last_sent_monotonic, self._min_resend_interval,
                notification.message, notification.recipient,
            )
            return False

        if not self.is_available():
            log.warning(
                "Skipping SMS (unavailable: %s). Intended message: %r to %s",
                self._load_error, notification.message, notification.recipient,
            )
            return False

        try:
            msg = self._client.messages.create(
                body=_TEMPLATE_BODY,
                from_=self._from_number,
                to=self._to_number,
            )
            log.info(
                "SMS sent (SID=%s): %r to %s",
                msg.sid, notification.message, notification.recipient,
            )
            self._last_sent_monotonic = now
            self.record_sent(notification.message, ok=True)
            return True
        except Exception as e:
            log.warning(
                "SMS send failed: %s. Intended message: %r to %s",
                e, notification.message, notification.recipient,
            )
            self.record_sent(notification.message, ok=False)
            return False
