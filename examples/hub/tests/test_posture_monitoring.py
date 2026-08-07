"""Deterministic tests for the explainable posture state machine."""

import os
import struct
import sys
import threading

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
    points[0], points[1], points[2] = [20, 270, .9], [22, 272, .9], [24, 272, .9]
    points[5], points[6] = [30, 280, .9], [35, 300, .9]
    points[11], points[12] = [145, 285, .9], [150, 305, .9]
    points[15], points[16] = [180, 290, .9], [185, 310, .9]
    return {"status": "ok", "keypoints": points}


def seated_pose():
    points = [[100.0, 200.0, 0.9] for _ in range(17)]
    points[0], points[1], points[2] = [100, 60, .9], [95, 65, .9], [105, 65, .9]
    points[5], points[6] = [90, 120, .9], [110, 120, .9]
    points[11], points[12] = [90, 245, .9], [110, 245, .9]
    points[13], points[14] = [75, 280, .9], [125, 280, .9]
    points[15], points[16] = [120, 280, .9], [80, 280, .9]
    return {"status": "ok", "keypoints": points}


def chair_slump_pose():
    """Slumped sideways in a chair, from live measurements: torso ~51 deg,
    hips at seat height (ratio ~0.58), box taller than wide, head still up."""
    points = [[100.0, 300.0, 0.9] for _ in range(17)]
    points[0], points[1], points[2] = [55, 110, .9], [50, 115, .9], [60, 115, .9]
    points[5], points[6] = [60, 150, .9], [70, 150, .9]     # shoulders dropped sideways
    points[11], points[12] = [160, 230, .9], [170, 230, .9]  # hips at chair height
    return {"status": "ok", "keypoints": points}


def forward_slump_pose():
    """Slumped straight forward, head hanging on the chest: torso stays
    near-vertical in the image (measured live: 0-9 deg while collapsed), so
    only the head-below-shoulders cue can see it."""
    points = [[100.0, 300.0, 0.9] for _ in range(17)]
    points[0], points[1], points[2] = [100, 160, .9], [95, 162, .9], [105, 162, .9]
    points[5], points[6] = [95, 150, .9], [105, 150, .9]     # head at/below this line
    points[11], points[12] = [95, 230, .9], [105, 230, .9]
    points[15], points[16] = [60, 350, .9], [140, 350, .9]
    return {"status": "ok", "keypoints": points}


def desk_sitting_pose():
    """Upright at a desk, legs stretched toward the camera: near-vertical
    torso but a WIDE keypoint box -- measured live while working motionless
    for 30+ s. Must never turn SUSPICIOUS."""
    points = [[100.0, 200.0, 0.9] for _ in range(17)]
    points[0], points[1], points[2] = [100, 40, .9], [95, 45, .9], [105, 45, .9]
    points[5], points[6] = [95, 100, .9], [105, 100, .9]
    points[11], points[12] = [95, 180, .9], [105, 180, .9]
    points[15], points[16] = [30, 290, .9], [370, 290, .9]  # feet out wide
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
    face = {"status": "known", "identity": "Priya"}

    first = machine.analyze(7, jpeg(), face, fallen_pose())
    assert first["state"] == "NORMAL"
    assert first["posture_score"] == 7

    clock.now = 3.1
    suspicious = machine.analyze(7, jpeg(), None, fallen_pose())
    assert suspicious["state"] == "SUSPICIOUS"
    assert suspicious["identity"] == "Priya"

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


def test_chair_slump_escalates_on_tilt_alone_then_stillness():
    # Regression for the acted heart-attack test: slumped sideways in a chair
    # (torso ~51 deg, hips at seat height) never triggered under the old
    # thresholds. Now a sustained tilt ALONE is enough for SUSPICIOUS -- i.e.
    # enough to ask the VLM -- and stillness on top of it reaches DANGER.
    clock = Clock()
    machine = PostureStateMachine(clock=clock)
    face = {"status": "known", "identity": "Priya"}

    first = machine.analyze(7, jpeg(), face, chair_slump_pose())
    assert first["state"] == "NORMAL"  # nothing is sustained yet
    assert first["posture_score"] == 3  # torso tilt only
    assert 45 <= first["torso_angle"] < 55  # under the OLD 55 deg threshold

    clock.now = 4.0  # tilt sustained past abnormal_seconds -> ask the VLM
    second = machine.analyze(7, jpeg(), None, chair_slump_pose())
    assert second["state"] == "SUSPICIOUS"
    assert second["posture_score"] == 3

    clock.now = 12.0  # tilted AND motionless -> DANGER
    result = machine.analyze(7, jpeg(), None, chair_slump_pose())
    assert result["state"] == "DANGER"
    assert result["posture_score"] == 6
    assert result["identity"] == "Priya"


def test_forward_slump_escalates_via_head_rule():
    # Second acted collapse style: slumping straight forward keeps the
    # shoulder->hip line vertical (torso ~0 deg live), so only the hanging
    # head distinguishes it from upright sitting. The head cue alone must
    # therefore be enough to reach the VLM.
    clock = Clock()
    machine = PostureStateMachine(clock=clock)
    face = {"status": "known", "identity": "Priya"}

    first = machine.analyze(9, jpeg(), face, forward_slump_pose())
    assert first["state"] == "NORMAL"
    assert first["head_dropped"] is True
    assert first["torso_angle"] < 5  # invisible to the tilt rule
    assert first["posture_score"] == 3  # head cue only

    clock.now = 4.0  # head down and sustained -> ask the VLM
    assert machine.analyze(9, jpeg(), None, forward_slump_pose())["state"] == "SUSPICIOUS"

    clock.now = 12.0  # head down AND motionless -> DANGER
    result = machine.analyze(9, jpeg(), None, forward_slump_pose())
    assert result["state"] == "DANGER"
    assert result["posture_score"] == 6


def test_wide_box_total_stillness_goes_suspicious():
    # The one signature present in every acted chair collapse at this camera
    # angle: a wide keypoint box gone COMPLETELY still (no tilt, head reading
    # above the shoulders). Weak cue + stillness must arm the timer and reach
    # SUSPICIOUS -- the VLM investigation filters out naps.
    clock = Clock()
    machine = PostureStateMachine(clock=clock)
    machine.analyze(2, jpeg(), {"status": "known", "identity": "Priya"},
                    desk_sitting_pose())
    clock.now = 2.0  # stillness timer arms on the second sample
    assert machine.analyze(2, jpeg(), None, desk_sitting_pose())["state"] == "NORMAL"
    clock.now = 8.0  # still >= 5s with a wide box -> SUSPICIOUS
    result = machine.analyze(2, jpeg(), None, desk_sitting_pose())
    assert result["state"] == "SUSPICIOUS"
    assert result["posture_score"] == 5


def test_fidgeting_desk_sitting_stays_normal():
    # Normal activity (measured): movement blips every few seconds keep
    # resetting the stillness timer, so the same wide-box pose with periodic
    # motion never becomes abnormal.
    clock = Clock()
    machine = PostureStateMachine(clock=clock)
    shifted = desk_sitting_pose()
    shifted["keypoints"][15] = [90, 290, .9]  # foot moved -- big normalized delta
    poses = [desk_sitting_pose(), shifted]
    machine.analyze(2, jpeg(), {"status": "known", "identity": "Priya"}, poses[0])
    for i, t in enumerate((3.0, 6.0, 9.0, 12.0, 15.0, 30.0, 60.0)):
        clock.now = t
        result = machine.analyze(2, jpeg(), None, poses[(i + 1) % 2])
        assert result["state"] == "NORMAL", t


def test_standing_still_narrow_box_stays_normal():
    # The floor under all the loosened thresholds: standing still (narrow box,
    # vertical torso, head up) has no postural cue at all, so even indefinite
    # stillness never arms the timer. Without this, every stationary person
    # alerts and the VLM -- the thing that decides -- gets drowned out.
    points = [[100.0, 200.0, 0.9] for _ in range(17)]
    points[0], points[1], points[2] = [100, 20, .9], [95, 25, .9], [105, 25, .9]
    points[5], points[6] = [95, 60, .9], [105, 60, .9]
    points[11], points[12] = [95, 180, .9], [105, 180, .9]
    points[15], points[16] = [95, 380, .9], [105, 380, .9]
    standing = {"status": "ok", "keypoints": points}

    clock = Clock()
    machine = PostureStateMachine(clock=clock)
    machine.analyze(3, jpeg(), {"status": "known", "identity": "Priya"}, standing)
    for t in (5.0, 20.0, 120.0):
        clock.now = t
        result = machine.analyze(3, jpeg(), None, standing)
        assert result["state"] == "NORMAL", t
        assert result["posture_score"] <= 3


def mild_lean_pose():
    """A 25 deg lean -- below even the OLD 45/55 deg tilt threshold, so it
    used to score nothing at all. Now it earns partial credit, which is
    enough to reach the VLM once the person also stops moving."""
    points = [[100.0, 300.0, 0.9] for _ in range(17)]
    points[0], points[1], points[2] = [100, 110, .9], [95, 112, .9], [105, 112, .9]
    points[5], points[6] = [95, 150, .9], [105, 150, .9]
    points[11], points[12] = [130, 230, .9], [145, 230, .9]
    return {"status": "ok", "keypoints": points}


def test_partial_cue_plus_stillness_reaches_the_vlm():
    # The core of the sensitive tuning: no single cue is convincing here --
    # a mild lean the old rules scored as zero, and stillness -- but together
    # they are worth a VLM opinion. Being wrong costs one VLM call; not
    # looking costs a missed collapse.
    clock = Clock()
    machine = PostureStateMachine(clock=clock)
    face = {"status": "known", "identity": "Priya"}

    first = machine.analyze(11, jpeg(), face, mild_lean_pose())
    assert 20 <= first["torso_angle"] < 35  # too mild for the full tilt cue
    assert first["score_breakdown"]["torso_horizontal"] == 2  # partial credit
    assert first["state"] == "NORMAL"

    clock.now = 1.0  # stillness timer arms on the second sample
    assert machine.analyze(11, jpeg(), None, mild_lean_pose())["state"] == "NORMAL"

    clock.now = 5.0  # leaning and motionless -> ask the VLM
    result = machine.analyze(11, jpeg(), None, mild_lean_pose())
    assert result["state"] == "SUSPICIOUS"
    assert result["posture_score"] == 5


def test_single_sample_blip_does_not_reset_timers():
    # One noisy frame at ~4 Hz (keypoint flicker reading as upright+moving)
    # must not zero the abnormal/stillness timers of an ongoing collapse --
    # the investigation trigger needs abnormal_duration to keep accumulating,
    # and a reset here delays the alert indefinitely for someone still down.
    clock = Clock()
    machine = PostureStateMachine(clock=clock)
    face = {"status": "known", "identity": "Priya"}

    machine.analyze(7, jpeg(), face, chair_slump_pose())
    clock.now = 4.0
    machine.analyze(7, jpeg(), None, chair_slump_pose())
    clock.now = 4.25  # single flicker frame within blip_grace_seconds
    machine.analyze(7, jpeg(), None, seated_pose())
    clock.now = 4.5
    machine.analyze(7, jpeg(), None, chair_slump_pose())

    clock.now = 12.0
    result = machine.analyze(7, jpeg(), None, chair_slump_pose())
    assert result["state"] == "DANGER"
    assert result["abnormal_duration_seconds"] == 12.0  # never reset


def test_sustained_recovery_does_reset_timers():
    # Sitting back upright for well past blip_grace_seconds is a real
    # recovery: the abnormal timer restarts from the next abnormal sample.
    clock = Clock()
    machine = PostureStateMachine(clock=clock)
    face = {"status": "known", "identity": "Priya"}

    machine.analyze(7, jpeg(), face, chair_slump_pose())
    clock.now = 4.0
    machine.analyze(7, jpeg(), None, chair_slump_pose())
    for t in (5.0, 6.0, 7.0, 8.0):
        clock.now = t
        machine.analyze(7, jpeg(), None, seated_pose())
    clock.now = 9.0
    result = machine.analyze(7, jpeg(), None, chair_slump_pose())
    assert result["state"] == "NORMAL"
    assert result["abnormal_duration_seconds"] == 0.0


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
    result = machine.analyze(2, jpeg(), {"status": "known", "identity": "Priya"}, seated_pose())
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


def test_security_policy_tracks_unknown_people_too():
    # Regression: posture used to be gated on a recognized face, which turned
    # monitoring OFF during a real collapse (face down/turned -> no_face,
    # tracker mints new ids). Every track with a pose is analyzed now.
    class Recorder:
        calls = []

        def analyze(self, *args):
            self.calls.append(args)
            return {"state": "NORMAL"}

    class IdleInvestigation:
        observed = []

        def observe(self, *args):
            self.observed.append(args)
            return None

    policy = SecurityPolicy.__new__(SecurityPolicy)
    policy.posture = Recorder()
    policy.investigation = IdleInvestigation()
    policy._mqtt = None
    policy._auto_buzzer_enabled = False
    policy._auto_buzzer_lock = threading.Lock()
    pose = {"status": "ok", "keypoints": [[0, 0, 1]] * 17}
    assert policy.analyze_track(1, b"image", {"status": "unknown"}, pose) == {"state": "NORMAL"}
    assert policy.analyze_track(2, b"image", None, pose) == {"state": "NORMAL"}
    result = policy.analyze_track(
        3, b"image", {"status": "known", "identity": "Priya"}, pose)
    assert result == {"state": "NORMAL"}
    assert len(policy.posture.calls) == 3
    assert len(policy.investigation.observed) == 3


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)} passed")
