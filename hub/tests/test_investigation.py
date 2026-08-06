"""Deterministic tests for the event-driven VLM investigation flow.

Success criteria under test (see apps/security/investigation.py):
  * NORMAL posture -> zero VLM calls
  * one persistent DANGER -> exactly one event, one capture command, one VLM
    call, at most one SMS
  * capture timeout -> the VLM still runs once, on a buffered frame
  * COOLDOWN suppresses new events until it elapses AND the person is NORMAL
"""

import os
import sys

HUB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HUB_DIR)

from framework import events, transport
from apps.security.investigation import InvestigationManager


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


class FakeVLM:
    def __init__(self, parsed=None, available=True):
        self.parsed = parsed if parsed is not None else {
            "classification": "EMERGENCY_LIKELY",
            "confidence": 0.9,
            "observations": ["person on the floor"],
            "recommended_action": "Check on them now.",
        }
        self.available = available
        self.calls = []

    def structured_query(self, image_path, prompt, max_new_tokens, **kwargs):
        self.calls.append({"image_path": image_path, "prompt": prompt})
        if not self.available:
            return {"available": False, "parsed": {}, "error": "unavailable",
                    "latency_s": None}
        return {"available": True, "parsed": dict(self.parsed),
                "text": "{}", "latency_s": 0.5, "error": None}


class FakeSMS:
    def __init__(self):
        self.sent = []

    def send(self, notification):
        self.sent.append(notification)
        return True


class FakeMQTT:
    def __init__(self, ok=True):
        self.ok = ok
        self.published = []

    def publish_command(self, device_id, command):
        self.published.append((device_id, command))
        return self.ok


def analysis(state="DANGER", abnormal=6.0, still=8.0, identity="Jogendra"):
    return {
        "identity": identity,
        "state": state,
        "posture_score": 10 if state == "DANGER" else 0,
        "abnormal_duration_seconds": abnormal,
        "duration_seconds": still,
        "torso_angle": 80.0,
        "score_breakdown": {"torso_horizontal": 3, "minimal_movement": 3},
    }


def make_manager(tmp_path, monkeypatch, vlm=None, mqtt=None):
    # Keep test artifacts (the saved investigation frame) out of hub/uploads.
    monkeypatch.setattr(transport, "UPLOAD_DIR", str(tmp_path))
    events.note_device("unoq-test")
    clock = Clock()
    vlm = vlm or FakeVLM()
    sms = FakeSMS()
    mqtt = mqtt or FakeMQTT()
    manager = InvestigationManager(vlm, sms, mqtt, clock=clock,
                                   spawn_threads=False)
    return manager, clock, vlm, sms, mqtt


def test_normal_posture_never_calls_vlm(tmp_path, monkeypatch):
    manager, clock, vlm, sms, mqtt = make_manager(tmp_path, monkeypatch)
    for _ in range(50):
        clock.now += 0.25
        manager.observe(4, b"jpeg", analysis(state="NORMAL", abnormal=0.0))
    assert vlm.calls == []
    assert mqtt.published == []
    assert sms.sent == []
    assert manager.snapshot()["state"] == "MONITORING"


def test_danger_below_persistence_does_not_trigger(tmp_path, monkeypatch):
    manager, clock, vlm, sms, mqtt = make_manager(tmp_path, monkeypatch)
    manager.observe(4, b"jpeg", analysis(abnormal=4.9))
    assert manager.snapshot()["state"] == "MONITORING"
    assert mqtt.published == []


def test_one_event_one_capture_one_vlm_call_one_sms(tmp_path, monkeypatch):
    manager, clock, vlm, sms, mqtt = make_manager(tmp_path, monkeypatch)

    status = manager.observe(4, b"danger-frame", analysis())
    assert status["state"] == "WAITING_FOR_CAPTURE"
    assert status["active_event_id"] == "event_001"
    assert status["event_target_identity"] == "Jogendra"
    assert len(mqtt.published) == 1
    device_id, command = mqtt.published[0]
    assert device_id == "unoq-test"
    assert command["command"] == "capture_investigation_image"
    assert command["event_id"] == "event_001"
    assert command["track_id"] == 4

    # Further DANGER samples while waiting must not open a second event.
    clock.now += 1.0
    manager.observe(4, b"jpeg", analysis())
    assert len(mqtt.published) == 1

    result = manager.on_capture("event_001", b"fresh-hd-frame")
    assert result["ok"] is True
    assert len(vlm.calls) == 1
    assert "Jogendra" in vlm.calls[0]["prompt"]
    assert "DANGER" in vlm.calls[0]["prompt"]
    assert len(sms.sent) == 1
    assert "EMERGENCY" in sms.sent[0].message
    assert "Jogendra" in sms.sent[0].message

    snap = manager.snapshot()
    assert snap["state"] == "COOLDOWN"
    assert snap["vlm_calls"] == 1
    assert snap["last_result"]["classification"] == "EMERGENCY_LIKELY"
    assert snap["last_result"]["image_source"] == "edge_capture"

    # A late duplicate capture is rejected and runs nothing.
    assert manager.on_capture("event_001", b"late")["ok"] is False
    assert len(vlm.calls) == 1


def test_capture_timeout_falls_back_to_buffered_frame(tmp_path, monkeypatch):
    manager, clock, vlm, sms, mqtt = make_manager(tmp_path, monkeypatch)
    manager.observe(4, b"danger-frame", analysis())
    assert manager.snapshot()["state"] == "WAITING_FOR_CAPTURE"

    clock.now += manager.capture_timeout_seconds + 0.1
    manager.check_capture_timeout()

    assert len(vlm.calls) == 1
    snap = manager.snapshot()
    assert snap["state"] == "COOLDOWN"
    assert snap["last_result"]["image_source"] == "buffered_frame"


def test_publish_failure_uses_buffered_frame_immediately(tmp_path, monkeypatch):
    mqtt = FakeMQTT(ok=False)
    manager, clock, vlm, sms, _ = make_manager(tmp_path, monkeypatch, mqtt=mqtt)
    manager.observe(4, b"danger-frame", analysis())
    # The deadline collapsed to "now"; the next sample runs the fallback.
    manager.observe(4, b"jpeg", analysis())
    assert len(vlm.calls) == 1
    assert manager.snapshot()["last_result"]["image_source"] == "buffered_frame"


def test_cooldown_requires_normal_before_reset(tmp_path, monkeypatch):
    manager, clock, vlm, sms, mqtt = make_manager(tmp_path, monkeypatch)
    manager.observe(4, b"jpeg", analysis())
    manager.on_capture("event_001", b"frame")
    assert manager.snapshot()["state"] == "COOLDOWN"

    # Still DANGER during cooldown: no new event.
    clock.now += 5.0
    manager.observe(4, b"jpeg", analysis())
    assert len(mqtt.published) == 1

    # Cooldown elapsed but the person is STILL in DANGER: keep suppressing.
    clock.now += manager.cooldown_seconds
    manager.observe(4, b"jpeg", analysis())
    assert manager.snapshot()["state"] == "COOLDOWN"
    assert len(mqtt.published) == 1

    # Back to NORMAL after the cooldown: fully reset...
    clock.now += 1.0
    manager.observe(4, b"jpeg", analysis(state="NORMAL", abnormal=0.0))
    assert manager.snapshot()["state"] == "MONITORING"

    # ...so a fresh persistent DANGER opens a NEW event.
    clock.now += 40.0  # also ages the old buffer entries
    manager.observe(4, b"jpeg", analysis())
    assert len(mqtt.published) == 2
    assert mqtt.published[1][1]["event_id"] == "event_002"


def test_uncertain_and_unavailable_send_manual_check_sms(tmp_path, monkeypatch):
    vlm = FakeVLM(parsed={"classification": "UNCERTAIN", "confidence": 0.4,
                          "observations": [], "recommended_action": ""})
    manager, clock, _, sms, _ = make_manager(tmp_path, monkeypatch, vlm=vlm)
    manager.observe(4, b"jpeg", analysis())
    manager.on_capture("event_001", b"frame")
    assert len(sms.sent) == 1
    assert "check on Jogendra" in sms.sent[0].message

    # VLM unavailable degrades to UNCERTAIN, never to silence.
    vlm2 = FakeVLM(available=False)
    manager2, clock2, _, sms2, _ = make_manager(tmp_path, monkeypatch, vlm=vlm2)
    manager2.observe(4, b"jpeg", analysis())
    manager2.on_capture("event_001", b"frame")
    assert manager2.snapshot()["last_result"]["classification"] == "UNCERTAIN"
    assert len(sms2.sent) == 1


def test_safe_likely_sends_no_sms(tmp_path, monkeypatch):
    vlm = FakeVLM(parsed={"classification": "SAFE_LIKELY", "confidence": 0.8,
                          "observations": ["stretching on a mat"],
                          "recommended_action": ""})
    manager, clock, _, sms, _ = make_manager(tmp_path, monkeypatch, vlm=vlm)
    manager.observe(4, b"jpeg", analysis())
    manager.on_capture("event_001", b"frame")
    assert sms.sent == []
    assert manager.snapshot()["last_result"]["classification"] == "SAFE_LIKELY"
    assert manager.snapshot()["state"] == "COOLDOWN"


def test_dashboard_manual_trigger_presents_result_without_sms(tmp_path, monkeypatch):
    manager, clock, vlm, sms, mqtt = make_manager(tmp_path, monkeypatch)
    result = manager.trigger_manual(source="dashboard")
    assert result["ok"] is True
    assert result["event_id"] == "event_001"
    assert result["capture_requested"] is True
    assert mqtt.published[0][1]["command"] == "capture_investigation_image"

    manager.on_capture("event_001", b"fresh-frame")
    assert len(vlm.calls) == 1
    assert "operator" in vlm.calls[0]["prompt"]

    snap = manager.snapshot()
    # Manual checks present on the dashboard, don't SMS, and skip cooldown.
    assert sms.sent == []
    assert snap["state"] == "MONITORING"
    assert snap["last_result"]["manual"] is True
    assert snap["last_result"]["source"] == "dashboard"
    assert snap["last_result"]["classification"] == "EMERGENCY_LIKELY"

    # Immediately triggerable again once finished.
    assert manager.trigger_manual(source="dashboard")["ok"] is True


def test_manual_trigger_rejected_while_investigation_active(tmp_path, monkeypatch):
    manager, clock, vlm, sms, mqtt = make_manager(tmp_path, monkeypatch)
    manager.observe(4, b"jpeg", analysis())
    assert manager.snapshot()["state"] == "WAITING_FOR_CAPTURE"

    result = manager.trigger_manual(source="dashboard")
    assert result["ok"] is False
    assert result["active_event_id"] == "event_001"
    assert len(mqtt.published) == 1  # no second capture command


def test_sms_capture_texts_vlm_reasoning_to_sender(tmp_path, monkeypatch):
    manager, clock, vlm, sms, mqtt = make_manager(tmp_path, monkeypatch)
    result = manager.trigger_manual(source="manual_sms",
                                    notify_recipient="+15551234567")
    assert result["ok"] is True
    manager.on_capture("event_001", b"fresh-frame")

    assert len(sms.sent) == 1
    reply = sms.sent[0]
    assert reply.recipient == "+15551234567"
    assert "EMERGENCY_LIKELY" in reply.message
    assert "person on the floor" in reply.message
    assert "Check on them now." in reply.message


def test_manual_trigger_during_cooldown_resumes_cooldown(tmp_path, monkeypatch):
    manager, clock, vlm, sms, mqtt = make_manager(tmp_path, monkeypatch)
    manager.observe(4, b"jpeg", analysis())
    manager.on_capture("event_001", b"frame")
    assert manager.snapshot()["state"] == "COOLDOWN"

    clock.now += 2.0
    result = manager.trigger_manual(source="dashboard")
    assert result["ok"] is True
    manager.on_capture("event_002", b"frame2")
    # The interrupted cooldown resumes: still no new automatic event allowed.
    assert manager.snapshot()["state"] == "COOLDOWN"

    clock.now += manager.cooldown_seconds
    manager.observe(4, b"jpeg", analysis(state="NORMAL", abnormal=0.0))
    assert manager.snapshot()["state"] == "MONITORING"


def test_policy_sms_capture_keyword(tmp_path, monkeypatch):
    from apps.security.policy import SecurityPolicy

    monkeypatch.setattr(transport, "UPLOAD_DIR", str(tmp_path))
    events.note_device("unoq-test")
    vlm, sms, mqtt = FakeVLM(), FakeSMS(), FakeMQTT()
    policy = SecurityPolicy(vlm, None, sms, None, mqtt=mqtt)
    policy.investigation = InvestigationManager(vlm, sms, mqtt, clock=Clock(),
                                                spawn_threads=False)

    # CAPTURE starts an investigation (no MQTT command via the framework's
    # reply path -- trigger_manual publishes the capture command itself).
    assert policy.on_sms_reply("+15551234567", "capture") is None
    assert mqtt.published[0][1]["command"] == "capture_investigation_image"
    ack = policy.reply_for_sms("+15551234567", "capture")
    assert "analyzing" in ack.lower()

    # A second CAPTURE while busy is refused with an explanatory reply.
    assert policy.on_sms_reply("+15551234567", "CAPTURE") is None
    assert len(mqtt.published) == 1
    busy = policy.reply_for_sms("+15551234567", "CAPTURE")
    assert "cannot" in busy.lower()

    # Capture arrives -> reasoning is texted back to the sender.
    policy.on_investigation_capture("event_001", b"fresh-frame")
    assert len(sms.sent) == 1
    assert sms.sent[0].recipient == "+15551234567"
    assert "EMERGENCY_LIKELY" in sms.sent[0].message


def test_evidence_buffer_is_frozen_at_trigger(tmp_path, monkeypatch):
    manager, clock, vlm, sms, mqtt = make_manager(tmp_path, monkeypatch)
    # ~1 fps buffer: 12 samples 1s apart -> deque keeps the newest 10.
    for i in range(12):
        clock.now = float(i)
        manager.observe(4, f"crop-{i}".encode(),
                        analysis(state="NORMAL", abnormal=0.0))
    clock.now = 12.0
    manager.observe(4, b"danger-frame", analysis())
    event = manager.active_event
    assert len(event["history"]) == manager.buffer_size
    assert event["history"][-1][1] == b"danger-frame"
    assert event["danger_frame"] == b"danger-frame"
