"""
test_policy.py — the application contract.

A Policy is the one thing every hub app writes. What must hold regardless of what
a specific app does: only `evaluate` is required, every other hook defaults to
"nothing to say" rather than raising, and evaluate sees a typed EdgeEvent rather
than a dict a caller happened to build.
"""

from __future__ import annotations

import pytest

from qonclave.core.models import EdgeEvent
from qonclave.hub.policy import Notification, Policy, Verdict


def _event(**kw) -> EdgeEvent:
    return EdgeEvent(event_id="e1", source_node_id="unoq-01", trigger="person_detected",
                     timestamp="2026-08-06T12:00:00Z", **kw)


class _MinimalPolicy(Policy):
    """Implements only the abstract method -- every other hook must still work
    via its default."""

    name = "minimal"

    def evaluate(self, event: EdgeEvent, image_path: str | None = None) -> Verdict:
        return Verdict(verified=False)


def test_cannot_instantiate_without_evaluate():
    class _NoEvaluate(Policy):
        pass

    with pytest.raises(TypeError):
        _NoEvaluate()


def test_minimal_policy_evaluates():
    policy = _MinimalPolicy()
    verdict = policy.evaluate(_event())

    assert verdict.verified is False
    assert verdict.confidence is None
    assert verdict.extra == {}


def test_default_hooks_all_return_none():
    policy = _MinimalPolicy()
    verdict = policy.evaluate(_event())
    event = _event()

    assert policy.command_for(verdict, event) is None
    assert policy.notify_for(verdict, event) is None
    assert policy.on_reply("+15551234567", "STOP") is None
    assert policy.reply_for("+15551234567", "STOP") is None
    assert policy.dashboard_state() is None
    assert policy.analyze_track(1, b"\xff\xd8", face=None, pose=None) is None
    assert policy.track_settings() is None
    assert policy.update_track_settings({"threshold": 1}) is None


def test_notification_recipient_is_optional():
    """Unlike the pre-lift hub-local Notification (recipient: str, required),
    the SDK's is optional -- a Policy can notify without knowing who to."""
    n = Notification(message="hello")

    assert n.recipient is None
    assert n.urgency == "normal"


def test_verdict_extra_is_merged_flat_by_convention():
    """extra isn't interpreted by Policy itself -- this just pins the shape a
    caller (framework/server.py) relies on: a plain dict, safe to ** into a
    response envelope."""
    verdict = Verdict(verified=True, confidence=0.9, alert="ok",
                      extra={"identity_status": "known", "identity_name": "alex"})

    assert verdict.extra == {"identity_status": "known", "identity_name": "alex"}
