# SPDX-License-Identifier: MPL-2.0

"""
placement.py — where this device's inference runs, decided per detection.

Replaces the hardcoded escalation in main.py:

    if best_confidence <= PERSON_CONFIDENCE_THRESHOLD:
        return

That line was the whole placement ladder: one metric, one threshold, compiled
in. This module keeps the same default behaviour and makes the decision
declarative, multi-metric, and inspectable — which is the point of
framework/docs/PLACEMENT.md's declared-vs-measured split:

    the app DECLARES   complexity, urgency, privacy, deadline
    the framework MEASURES   confidence, power, reachability, elapsed time

Hand-written against the contract rather than importing qonclave.placement, for
the same reason hub_protocol.py is: the SDK does not belong on flashed firmware.
The shape deliberately mirrors `PlacementPolicy.decide` so the two read alike —
see framework/sdk/python/src/qonclave/placement/policy.py.

What this deliberately does NOT do is decide *how* to run anything. It answers
one question — does this observation stay here, or go up a tier — and returns
the reason, so a surprising decision can be explained rather than guessed at.
"""

from __future__ import annotations

import time
from typing import NamedTuple

# Tier names as they appear on the wire (spec/v1/dictionary.md).
TIER_EDGE = "edge"
TIER_HUB = "hub"


class Decision(NamedTuple):
    """Where this observation runs, and why."""

    tier: str
    reason: str

    @property
    def escalates(self) -> bool:
        return self.tier != TIER_EDGE


class EscalationPolicy:
    """Decides whether one local detection is worth a hub round trip.

    Every rule here was previously either a bare constant in main.py or absent:

    * `confidence_threshold` was PERSON_CONFIDENCE_THRESHOLD
    * `min_interval_s` was HUB_EVENT_HYSTERESIS_SEC, applied by a lock and a
      module-level timestamp at the call site
    * `battery_floor_pct` did not exist — the device escalated at any charge
    * `deadline_ms` did not exist — nothing bounded how long the answer was
      worth waiting for
    """

    def __init__(self, *, confidence_threshold: float = 0.7,
                 min_interval_s: float = 10.0,
                 deadline_ms: int = 3000,
                 battery_floor_pct: float | None = None) -> None:
        self.confidence_threshold = confidence_threshold
        self.min_interval_s = min_interval_s
        self.deadline_ms = deadline_ms
        self.battery_floor_pct = battery_floor_pct
        self._last_escalation_at: float | None = None

    def decide(self, *, confidence: float,
               now: float | None = None,
               battery_pct: float | None = None,
               hub_reachable: bool = True) -> Decision:
        """Where this detection should be verified.

        Order matters. Cheap local facts are checked before anything that would
        cost radio time, so a device that cannot or should not escalate never
        pays to find that out.
        """
        now = time.monotonic() if now is None else now

        if confidence <= self.confidence_threshold:
            return Decision(TIER_EDGE,
                            f"confidence {confidence:.2f} <= threshold "
                            f"{self.confidence_threshold:.2f}")

        if not hub_reachable:
            # Not a failure — the local detector already produced an answer.
            # Escalation is an upgrade, and an unavailable upgrade is not an
            # error, which is why this returns a tier rather than raising.
            return Decision(TIER_EDGE, "hub unreachable; keeping the local verdict")

        if (self.battery_floor_pct is not None and battery_pct is not None
                and battery_pct < self.battery_floor_pct):
            return Decision(TIER_EDGE,
                            f"battery {battery_pct:.0f}% below floor "
                            f"{self.battery_floor_pct:.0f}%; not spending radio")

        if self._last_escalation_at is not None:
            since = now - self._last_escalation_at
            if since < self.min_interval_s:
                return Decision(TIER_EDGE,
                                f"last escalation {since:.1f}s ago, "
                                f"minimum interval {self.min_interval_s:.0f}s")

        return Decision(TIER_HUB,
                        f"confidence {confidence:.2f} > threshold "
                        f"{self.confidence_threshold:.2f}")

    def committed(self, now: float | None = None) -> None:
        """Record that an escalation actually happened.

        Separate from `decide` so a decision that is later abandoned — no frame
        to send, a failed encode — does not start the interval clock. Calling
        `decide` must have no side effects, or a dry run changes behaviour.
        """
        self._last_escalation_at = time.monotonic() if now is None else now

    def task_descriptor(self, *, elapsed_ms: int = 0) -> dict:
        """The `task` block carried on the wire (edge-event.schema.json).

        `remaining_ms` is the deadline minus what this tier already spent. The
        hub needs it because it cannot otherwise know how much of the budget is
        left: without it every tier re-plans against the original deadline and
        the ladder quietly blows an SLA nobody is tracking.
        """
        return {
            "complexity": "vlm_reason",
            "urgency": "normal",
            "privacy": "unrestricted",
            "use_case": "person_verification",
            "deadline_ms": self.deadline_ms,
            "remaining_ms": max(0, self.deadline_ms - elapsed_ms),
            "hops": [TIER_EDGE],
        }
