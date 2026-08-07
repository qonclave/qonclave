"""
test_placement_wiring.py — hub/apps/security/placement.py is a real consumer.

Until now `SecurityPlacement` had zero callers outside its own file — clean
usage of qonclave.placement, but unproven under real traffic. create_app()
now accepts an optional `placement` and runs it (observability only, no
compute tier exists yet) inside /edge/event. This is the proof it actually
works end to end: a real EdgeEvent, a real InferenceTask built via
InferenceTask.from_event, a real qonclave.placement.resolve() call, against
the app's own SecurityPlacement.decide().
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apps.security.placement import SecurityPlacement  # noqa: E402
from framework.policy import Policy, Verdict  # noqa: E402
from framework.server import create_app  # noqa: E402


class _StubPolicy(Policy):
    name = "stub"

    def evaluate(self, event, image_path=None):
        return Verdict(verified=False, confidence=None, alert="stub")


class _StubBackend:
    def status(self):
        return {"available": False}


class _RecordingPlacement(SecurityPlacement):
    """The real SecurityPlacement, plus a record of every decide() call so the
    test can assert it actually ran against a real task/tiers pair."""

    def __init__(self):
        super().__init__()
        self.calls: list[tuple] = []

    def decide(self, task, tiers):
        self.calls.append((task, tiers))
        return super().decide(task, tiers)


@pytest.fixture
def placement():
    return _RecordingPlacement()


@pytest.fixture
def client(tmp_path, placement):
    static = tmp_path / "static"
    static.mkdir()
    (static / "dashboard.html").write_text("<html></html>", encoding="utf-8")
    app = create_app(
        policy=_StubPolicy(), vlm=_StubBackend(), mqtt=_StubBackend(),
        sms=_StubBackend(), static_dir=str(static), placement=placement,
    )
    app.config["TESTING"] = True
    return app.test_client()


def test_edge_event_still_succeeds_with_placement_wired(client):
    resp = client.post(
        "/edge/event?device_id=unoq-01&event_type=person_detected&edge_confidence=0.9",
        data=b"\xff\xd8\xff\xe0 not really a jpeg",
        content_type="image/jpeg",
    )
    assert resp.status_code == 200
    assert resp.get_json()["received"] is True


def test_placement_decide_runs_against_a_real_task_and_tierset(client, placement):
    client.post(
        "/edge/event?device_id=unoq-01&event_type=person_detected&edge_confidence=0.9",
        data=b"\xff\xd8\xff\xe0 not really a jpeg",
        content_type="image/jpeg",
    )

    assert len(placement.calls) == 1
    task, tiers = placement.calls[0]
    # framework/server.py builds this generically (InferenceTask.from_event with no
    # default_use_case) -- an app-specific default like SecurityPlacement's own
    # "person_verification" only applies where the app itself calls task_from_event
    # (e.g. inside its Policy), not through this framework-level observability hook.
    assert task.descriptor.use_case is None
    assert task.descriptor.complexity.name == "VLM_REASON"
    assert tiers.local.node_id == "hub"


def test_edge_event_survives_a_placement_decision_error(client, placement, monkeypatch):
    """Placement is observability only today -- a bug in it must never block
    ingestion, since there is nowhere else for a single-laptop deployment to
    send the event anyway."""
    def _boom(task, tiers):
        raise RuntimeError("placement blew up")

    monkeypatch.setattr(placement, "decide", _boom)

    resp = client.post(
        "/edge/event?device_id=unoq-01&event_type=person_detected&edge_confidence=0.9",
        data=b"\xff\xd8\xff\xe0 not really a jpeg",
        content_type="image/jpeg",
    )
    assert resp.status_code == 200
    assert resp.get_json()["received"] is True
