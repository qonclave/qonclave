"""Deterministic tests for the event-driven VLM investigation flow.

Success criteria under test (see apps/security/investigation.py):
  * NORMAL posture -> zero VLM calls
  * one persistent DANGER -> exactly one event, one capture command, one VLM
    call, at most one SMS
  * capture timeout -> the VLM still runs once, on a buffered frame
  * COOLDOWN suppresses new events while it lasts; a person STILL abnormal
    when it elapses is re-investigated (never suppressed forever)
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
        # Shaped like a compliant reply: exactly two short sentences, the first
        # naming the person, no numbers or sensor terms.
        self.parsed = parsed if parsed is not None else {
            "classification": "EMERGENCY_LIKELY",
            "confidence": 0.9,
            "observations": ["Jogendra is lying on the floor by the sofa.",
                             "The room is otherwise empty."],
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
    manager.observe(4, b"jpeg", analysis(abnormal=1.9))
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
    # Posture flagged this person: the edge closes distance before capturing
    # so the VLM gets a close-up instead of a distant smudge.
    assert command["approach"] is True

    # Further DANGER samples while waiting must not open a second event.
    clock.now += 1.0
    manager.observe(4, b"jpeg", analysis())
    assert len(mqtt.published) == 1

    result = manager.on_capture("event_001", b"fresh-hd-frame")
    assert result["ok"] is True
    assert len(vlm.calls) == 1

    prompt = vlm.calls[0]["prompt"]
    # The prompt must name the person and forbid the generic fallbacks, or the
    # reply comes back as "A person appears to be..." with no name in it.
    assert "Jogendra" in prompt
    assert '"a person"' in prompt
    # ...and it must NOT hand over telemetry to parrot back. The model was
    # reporting "The torso angle is 12.0 degrees from vertical" in alerts meant
    # for a relative, because the prompt fed it exactly that.
    assert "torso" not in prompt.lower()
    assert "DANGER" not in prompt
    assert "80.0" not in prompt  # the analysis() fixture's torso_angle

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


def test_cooldown_reinvestigates_while_still_abnormal(tmp_path, monkeypatch):
    manager, clock, vlm, sms, mqtt = make_manager(tmp_path, monkeypatch)
    manager.observe(4, b"jpeg", analysis())
    manager.on_capture("event_001", b"frame")
    assert manager.snapshot()["state"] == "COOLDOWN"

    # Still DANGER during cooldown: no new event yet.
    clock.now += manager.cooldown_seconds / 2
    manager.observe(4, b"jpeg", analysis())
    assert len(mqtt.published) == 1

    # Cooldown elapsed and the person is STILL in DANGER: a fresh
    # investigation opens immediately. One SAFE_LIKELY misread (or an
    # unanswered UNCERTAIN SMS) must never silence monitoring for good
    # while someone is down.
    clock.now += manager.cooldown_seconds
    manager.observe(4, b"jpeg", analysis())
    assert manager.snapshot()["state"] == "WAITING_FOR_CAPTURE"
    assert len(mqtt.published) == 2
    assert mqtt.published[1][1]["event_id"] == "event_002"


def test_cooldown_resets_once_person_normal(tmp_path, monkeypatch):
    manager, clock, vlm, sms, mqtt = make_manager(tmp_path, monkeypatch)
    manager.observe(4, b"jpeg", analysis())
    manager.on_capture("event_001", b"frame")
    assert manager.snapshot()["state"] == "COOLDOWN"

    # Back to NORMAL after the cooldown: fully reset...
    clock.now += manager.cooldown_seconds + 1.0
    manager.observe(4, b"jpeg", analysis(state="NORMAL", abnormal=0.0))
    assert manager.snapshot()["state"] == "MONITORING"

    # ...so a fresh persistent DANGER opens a NEW event.
    clock.now += 40.0  # also ages the old buffer entries
    manager.observe(4, b"jpeg", analysis())
    assert len(mqtt.published) == 2
    assert mqtt.published[1][1]["event_id"] == "event_002"


def test_uncertain_and_unavailable_send_no_sms(tmp_path, monkeypatch):
    # Only EMERGENCY_LIKELY routes an SMS; UNCERTAIN still classifies and
    # records normally, it just never dispatches a message.
    vlm = FakeVLM(parsed={"classification": "UNCERTAIN", "confidence": 0.4,
                          "observations": [], "recommended_action": ""})
    manager, clock, _, sms, _ = make_manager(tmp_path, monkeypatch, vlm=vlm)
    manager.observe(4, b"jpeg", analysis())
    manager.on_capture("event_001", b"frame")
    assert manager.snapshot()["last_result"]["classification"] == "UNCERTAIN"
    assert sms.sent == []

    # VLM unavailable degrades to UNCERTAIN, never to silence -- and still
    # sends no SMS, same as any other UNCERTAIN result.
    vlm2 = FakeVLM(available=False)
    manager2, clock2, _, sms2, _ = make_manager(tmp_path, monkeypatch, vlm=vlm2)
    manager2.observe(4, b"jpeg", analysis())
    manager2.on_capture("event_001", b"frame")
    assert manager2.snapshot()["last_result"]["classification"] == "UNCERTAIN"
    assert sms2.sent == []


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


def test_alert_is_two_plain_sentences_with_no_telemetry(tmp_path, monkeypatch):
    # The complaint that prompted this: alerts read like sensor dumps --
    # "The torso angle is 12.0 degrees from vertical, which may indicate a
    # fall.; ..." -- four clauses of engineering, semicolon-joined.
    manager, clock, vlm, sms, _ = make_manager(tmp_path, monkeypatch)
    manager.observe(4, b"jpeg", analysis())
    manager.on_capture("event_001", b"frame")

    outcome = manager.snapshot()["last_result"]
    assert len(outcome["observations"]) == 2  # capped, not merely requested
    message = sms.sent[0].message
    assert ";" not in message
    assert "torso" not in message.lower()
    assert "degree" not in message.lower()
    assert message == ("EMERGENCY: Jogendra is lying on the floor by the sofa. "
                       "Check on them now.")


def test_extra_observations_are_truncated_to_two(tmp_path, monkeypatch):
    # A model that ignores "exactly two" must not produce a wall of text.
    vlm = FakeVLM(parsed={
        "classification": "EMERGENCY_LIKELY", "confidence": 0.9,
        "observations": ["One.", "Two.", "Three.", "Four."],
        "recommended_action": "Go now.",
    })
    manager, _, _, sms, _ = make_manager(tmp_path, monkeypatch, vlm=vlm)
    manager.observe(4, b"jpeg", analysis())
    manager.on_capture("event_001", b"frame")

    assert manager.snapshot()["last_result"]["observations"] == ["One.", "Two."]
    assert "Three." not in sms.sent[0].message


def test_name_is_added_when_the_model_omits_it(tmp_path, monkeypatch):
    # The prompt demands the name, but an emergency text that never says WHO is
    # useless, so the name is guaranteed rather than trusted.
    vlm = FakeVLM(parsed={
        "classification": "EMERGENCY_LIKELY", "confidence": 0.9,
        "observations": ["A person is slumped on the floor.", "The room is dim."],
        "recommended_action": "Check now.",
    })
    manager, _, _, sms, _ = make_manager(tmp_path, monkeypatch, vlm=vlm)
    manager.observe(4, b"jpeg", analysis())
    manager.on_capture("event_001", b"frame")
    assert sms.sent[0].message.startswith("EMERGENCY: Jogendra: ")


def test_name_is_not_duplicated_when_the_model_complies(tmp_path, monkeypatch):
    manager, _, _, sms, _ = make_manager(tmp_path, monkeypatch)
    manager.observe(4, b"jpeg", analysis())
    manager.on_capture("event_001", b"frame")
    assert sms.sent[0].message.count("Jogendra") == 1


def test_unidentified_person_never_triggers_the_vlm(tmp_path, monkeypatch):
    # An automatic investigation names someone in the alert, and this hub
    # only knows how to name enrolled people -- a track with no resolvable
    # identity at all (never enrolled, no prior known sighting on this
    # track_id) must never open one or call the VLM. Contrast with
    # test_name_is_recovered_from_the_track_when_the_sample_has_none, where
    # the track WAS known earlier and still gets investigated.
    manager, _, vlm, sms, mqtt = make_manager(tmp_path, monkeypatch)
    manager.observe(4, b"jpeg", analysis(identity="Unknown"))

    assert manager.snapshot()["state"] == "MONITORING"
    assert vlm.calls == []
    assert mqtt.published == []
    assert sms.sent == []


def test_name_is_recovered_from_the_track_when_the_sample_has_none(tmp_path,
                                                                  monkeypatch):
    # A collapsing person stops being face-recognizable, so the triggering
    # sample often carries no identity -- but the hub knew them seconds ago.
    from framework import track_store

    track_store.clear()
    track_store.record(4, {"identity": "Jogendra", "status": "known"}, None)
    try:
        manager, _, vlm, sms, _ = make_manager(tmp_path, monkeypatch)
        manager.observe(4, b"jpeg", analysis(identity="Unknown"))
        manager.on_capture("event_001", b"frame")

        assert manager.snapshot()["last_result"]["identity"] == "Jogendra"
        assert "Jogendra" in vlm.calls[0]["prompt"]
    finally:
        track_store.clear()


def test_low_confidence_safe_likely_still_demotes_to_uncertain(tmp_path, monkeypatch):
    # A hedged "looks fine" is not evidence that someone is fine. Posture
    # already saw a persistent DANGER, so an unconvinced SAFE_LIKELY must not
    # be reported as SAFE_LIKELY on the dashboard -- it demotes to UNCERTAIN,
    # even though (like any UNCERTAIN result) that sends no SMS.
    vlm = FakeVLM(parsed={"classification": "SAFE_LIKELY", "confidence": 0.35,
                          "observations": ["person is seated, hard to tell"],
                          "recommended_action": ""})
    manager, clock, _, sms, _ = make_manager(tmp_path, monkeypatch, vlm=vlm)
    manager.observe(4, b"jpeg", analysis())
    manager.on_capture("event_001", b"frame")

    assert manager.snapshot()["last_result"]["classification"] == "UNCERTAIN"
    assert sms.sent == []

    # A missing confidence field is treated the same way.
    vlm2 = FakeVLM(parsed={"classification": "SAFE_LIKELY",
                           "observations": [], "recommended_action": ""})
    manager2, _, _, sms2, _ = make_manager(tmp_path, monkeypatch, vlm=vlm2)
    manager2.observe(4, b"jpeg", analysis())
    manager2.on_capture("event_001", b"frame")
    assert manager2.snapshot()["last_result"]["classification"] == "UNCERTAIN"
    assert sms2.sent == []


def test_vlm_crash_does_not_strand_the_state_machine(tmp_path, monkeypatch):
    # A crash mid-investigation must never leave the machine in VLM_RUNNING:
    # that blocks every future investigation, which is permanent silence --
    # the one failure mode this system cannot afford.
    class ExplodingVLM(FakeVLM):
        def structured_query(self, *a, **kw):
            raise RuntimeError("boom")

    manager, clock, _, sms, mqtt = make_manager(tmp_path, monkeypatch,
                                                vlm=ExplodingVLM())
    manager.observe(4, b"jpeg", analysis())
    manager.on_capture("event_001", b"frame")

    snap = manager.snapshot()
    assert snap["state"] == "COOLDOWN"
    assert snap["vlm_in_progress"] is False

    # ...and once the cooldown elapses, a still-abnormal person is retried.
    clock.now += manager.cooldown_seconds + 1.0
    manager.observe(4, b"jpeg", analysis())
    assert manager.snapshot()["state"] == "WAITING_FOR_CAPTURE"
    assert len(mqtt.published) == 2


def test_dashboard_manual_trigger_presents_result_without_sms(tmp_path, monkeypatch):
    manager, clock, vlm, sms, mqtt = make_manager(tmp_path, monkeypatch)
    result = manager.trigger_manual(source="dashboard")
    assert result["ok"] is True
    assert result["event_id"] == "event_001"
    assert result["capture_requested"] is True
    assert mqtt.published[0][1]["command"] == "capture_investigation_image"
    # An operator wants the scene as it is; clicking must not drive the robot.
    assert mqtt.published[0][1]["approach"] is False

    manager.on_capture("event_001", b"fresh-frame")
    assert len(vlm.calls) == 1
    assert "on-demand check" in vlm.calls[0]["prompt"]

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
    # Plain prose a relative can read, not the raw classification token -- but
    # the severity still has to survive, or an emergency reads as an all-clear.
    assert reply.message.startswith("EMERGENCY: ")
    assert "EMERGENCY_LIKELY" not in reply.message
    assert "Jogendra is lying on the floor by the sofa." in reply.message
    assert "Check on them now." in reply.message
    assert ";" not in reply.message  # the old "; ".join formatting is gone


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
    assert policy.on_reply("+15551234567", "capture") is None
    assert mqtt.published[0][1]["command"] == "capture_investigation_image"
    ack = policy.reply_for("+15551234567", "capture")
    assert "analyzing" in ack.lower()

    # A second CAPTURE while busy is refused with an explanatory reply.
    assert policy.on_reply("+15551234567", "CAPTURE") is None
    assert len(mqtt.published) == 1
    busy = policy.reply_for("+15551234567", "CAPTURE")
    assert "cannot" in busy.lower()

    # Capture arrives -> reasoning is texted back to the sender.
    policy.on_investigation_capture("event_001", b"fresh-frame")
    assert len(sms.sent) == 1
    assert sms.sent[0].recipient == "+15551234567"
    assert sms.sent[0].message.startswith("EMERGENCY: ")


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
