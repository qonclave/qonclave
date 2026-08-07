# SPDX-License-Identifier: MPL-2.0

"""
test_placement.py — the escalation decision, which used to be one inlined `if`.

The behaviour these pin down is the behaviour main.py had before placement
existed, plus the two rules it never had (battery floor, hub reachability). If
the defaults ever stop matching the old constants, the demo changes character
without anyone deciding to change it.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))
from placement import TIER_EDGE, TIER_HUB, EscalationPolicy  # noqa: E402


def policy(**kw):
    kw.setdefault("confidence_threshold", 0.7)
    kw.setdefault("min_interval_s", 10.0)
    return EscalationPolicy(**kw)


# --- the behaviour that replaced `if best_confidence <= THRESHOLD` -----------

def test_below_threshold_stays_on_the_edge():
    d = policy().decide(confidence=0.5, now=100.0)
    assert d.tier == TIER_EDGE
    assert not d.escalates


def test_above_threshold_escalates():
    d = policy().decide(confidence=0.9, now=100.0)
    assert d.tier == TIER_HUB
    assert d.escalates


def test_exactly_at_threshold_does_not_escalate():
    """The original used `<=`, so the boundary is inclusive-stay. Flipping this
    would double escalation traffic on a camera sitting right at the line."""
    assert policy().decide(confidence=0.7, now=100.0).tier == TIER_EDGE


# --- hysteresis, which used to be a module global and a lock ----------------

def test_second_detection_inside_the_interval_is_suppressed():
    p = policy()
    assert p.decide(confidence=0.9, now=100.0).escalates
    p.committed(now=100.0)
    assert not p.decide(confidence=0.9, now=105.0).escalates


def test_detection_after_the_interval_escalates_again():
    p = policy()
    p.committed(now=100.0)
    assert p.decide(confidence=0.9, now=111.0).escalates


def test_decide_is_side_effect_free():
    """An escalation that is decided but then abandoned — no frame, a failed
    encode — must not start the interval clock. If `decide` committed, a dry run
    would change behaviour."""
    p = policy()
    for _ in range(5):
        assert p.decide(confidence=0.9, now=100.0).escalates
    p.committed(now=100.0)
    assert not p.decide(confidence=0.9, now=101.0).escalates


# --- rules the hardcoded version never had ----------------------------------

def test_unreachable_hub_keeps_the_local_verdict():
    """Not an error. The local detector already produced an answer; escalation
    is an upgrade, and an unavailable upgrade is not a failure."""
    d = policy().decide(confidence=0.9, now=100.0, hub_reachable=False)
    assert d.tier == TIER_EDGE
    assert "unreachable" in d.reason


def test_low_battery_suppresses_escalation():
    d = policy(battery_floor_pct=20.0).decide(confidence=0.9, now=100.0, battery_pct=15.0)
    assert d.tier == TIER_EDGE
    assert "battery" in d.reason


def test_battery_rule_is_off_unless_configured():
    """Default deployment is mains-powered; the floor must not fire by accident."""
    assert policy().decide(confidence=0.9, now=100.0, battery_pct=2.0).escalates


def test_battery_unknown_does_not_suppress():
    d = policy(battery_floor_pct=20.0).decide(confidence=0.9, now=100.0, battery_pct=None)
    assert d.escalates


# --- every decision explains itself -----------------------------------------

@pytest.mark.parametrize("kw", [
    {"confidence": 0.5},
    {"confidence": 0.9},
    {"confidence": 0.9, "hub_reachable": False},
])
def test_every_decision_carries_a_reason(kw):
    """`qonclave placement-explain` exists because a placement you cannot
    explain is one you debug by guessing."""
    assert policy().decide(now=100.0, **kw).reason


# --- the task descriptor that travels on the wire ---------------------------

def test_task_descriptor_uses_the_spec_string_enums():
    """The schema defines complexity and urgency as strings, not ints."""
    t = policy().task_descriptor()
    assert t["complexity"] == "vlm_reason"
    assert t["urgency"] == "normal"
    assert t["privacy"] == "unrestricted"


def test_remaining_budget_is_deducted():
    t = EscalationPolicy(deadline_ms=3000).task_descriptor(elapsed_ms=1200)
    assert t["deadline_ms"] == 3000
    assert t["remaining_ms"] == 1800


def test_remaining_never_goes_negative():
    t = EscalationPolicy(deadline_ms=1000).task_descriptor(elapsed_ms=5000)
    assert t["remaining_ms"] == 0


def test_hops_records_this_tier():
    assert policy().task_descriptor()["hops"] == [TIER_EDGE]
