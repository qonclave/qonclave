"""Deterministic tests for the explainable posture state machine."""

import os
import struct
import sys

HUB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HUB_DIR)

from apps.security.posture import PostureSettings, PostureStateMachine
from apps.security.policy import SecurityPolicy


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def jpeg(width=200, height=400):
    return b"\xff\xd8\xff\xc0\x00\x11\x08" + struct.pack(">HH", height, width) + b"\x03" + b"\x00" * 20


def fallen_pose():
    points = [[100.0, 300.0, 0.9] for _ in range(17)]
    points[5], points[6] = [30, 280, .9], [35, 300, .9]
    points[11], points[12] = [145, 285, .9], [150, 305, .9]
    points[15], points[16] = [180, 290, .9], [185, 310, .9]
    return {"status": "ok", "keypoints": points}


def seated_pose():
    points = [[100.0, 200.0, 0.9] for _ in range(17)]
    points[5], points[6] = [90, 120, .9], [110, 120, .9]
    points[11], points[12] = [90, 245, .9], [110, 245, .9]
    points[13], points[14] = [75, 280, .9], [125, 280, .9]
    points[15], points[16] = [120, 280, .9], [80, 280, .9]
    return {"status": "ok", "keypoints": points}


def scaled_pose(pose, factor):
    return {
        **pose,
        "keypoints": [[x * factor, y * factor, score]
                      for x, y, score in pose["keypoints"]],
    }


def test_fall_requires_abnormal_then_motionless_durations():
    clock = Clock()
    machine = PostureStateMachine(PostureSettings(abnormal_seconds=3, motionless_seconds=5), clock)
    face = {"status": "known", "identity": "Jogendra"}

    first = machine.analyze(7, jpeg(), face, fallen_pose())
    assert first["state"] == "NORMAL"
    assert first["posture_score"] == 7

    clock.now = 3.1
    suspicious = machine.analyze(7, jpeg(), None, fallen_pose())
    assert suspicious["state"] == "SUSPICIOUS"
    assert suspicious["identity"] == "Jogendra"

    clock.now = 8.2
    danger = machine.analyze(7, jpeg(), None, fallen_pose())
    assert danger["state"] == "DANGER"
    assert danger["posture_score"] == 10
    assert danger["duration_seconds"] == 5.1


def test_known_identity_keeps_timers_when_track_id_changes():
    clock = Clock()
    machine = PostureStateMachine(
        PostureSettings(abnormal_seconds=3, motionless_seconds=5), clock)
    face = {"status": "known", "identity": "Alice"}

    assert machine.analyze(4, jpeg(), face, fallen_pose())["state"] == "NORMAL"
    clock.now = 3.1
    suspicious = machine.analyze(17, jpeg(), face, fallen_pose())
    assert suspicious["state"] == "SUSPICIOUS"
    assert suspicious["abnormal_duration_seconds"] == 3.1

    clock.now = 8.2
    danger = machine.analyze(51, jpeg(), face, fallen_pose())
    assert danger["state"] == "DANGER"
    assert danger["duration_seconds"] == 5.1


def test_crop_resize_does_not_look_like_person_movement():
    clock = Clock()
    machine = PostureStateMachine(clock=clock)
    face = {"status": "known", "identity": "Alice"}

    machine.analyze(3, jpeg(), face, fallen_pose())
    clock.now = 1.0
    result = machine.analyze(
        9, jpeg(width=400, height=800), face, scaled_pose(fallen_pose(), 2))

    assert result["movement"] == "Low"
    assert result["movement_ratio"] == 0.0


def test_vertical_bent_knee_sitting_stays_normal():
    clock = Clock()
    machine = PostureStateMachine(clock=clock)
    result = machine.analyze(2, jpeg(), {"status": "known", "identity": "Jogendra"}, seated_pose())
    assert result["normal_seated"] is True
    assert result["posture_score"] == 0
    clock.now = 20
    assert machine.analyze(2, jpeg(), None, seated_pose())["state"] == "NORMAL"


def test_settings_are_validated_and_updated():
    machine = PostureStateMachine()
    assert machine.update_settings({"abnormal_seconds": 2.5})["abnormal_seconds"] == 2.5
    try:
        machine.update_settings({"motionless_seconds": 0})
    except ValueError:
        pass
    else:
        raise AssertionError("zero duration should be rejected")


def test_security_policy_only_tracks_known_people():
    class Recorder:
        calls = []

        def analyze(self, *args):
            self.calls.append(args)
            return {"state": "NORMAL"}

    policy = SecurityPolicy.__new__(SecurityPolicy)
    policy.posture = Recorder()
    pose = {"status": "ok", "keypoints": [[0, 0, 1]] * 17}
    assert policy.analyze_track(1, b"image", {"status": "unknown"}, pose) is None
    assert policy.analyze_track(2, b"image", None, pose) is None
    assert policy.posture.calls == []
    result = policy.analyze_track(
        3, b"image", {"status": "known", "identity": "Jogendra"}, pose)
    assert result == {"state": "NORMAL"}
    assert len(policy.posture.calls) == 1


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)} passed")
