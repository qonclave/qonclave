"""
qonclave.placement — decide which tier runs a task.

The developer writes a `PlacementPolicy` subclass. The framework measures the facts, walks the
fallback chain, enforces privacy denials, deducts the deadline across hops, and dispatches:

    framework owns                          developer owns
    ------------------------------------    -------------------------
    measuring power / thermal / load / rtt   the decision
    walking the fallback chain               the thresholds
    enforcing `deny`                         which metrics matter
    deducting the deadline
    dispatching to the resolved backend

There is deliberately no rule DSL and no ruleset schema — placement decisions are code, in the
same idiom as `hub.Policy`, so there is one thing to learn rather than two.

    from qonclave.placement import PlacementPolicy, Placement, Tier

    class MyPlacement(PlacementPolicy):
        def decide(self, task, tiers):
            if task.descriptor.privacy == "no_egress":
                return Placement(Tier.HUB, deny=[Tier.COMPUTE])
            if tiers.local.power.is_constrained:
                return Placement(Tier.HUB, fallback=[Tier.COMPUTE])
            return Placement(Tier.EDGE, fallback=[Tier.HUB])

Docs: framework/docs/PLACEMENT.md
"""

from .ladder import PlacementDeferred, PlacementError, Resolution, resolve
from .policy import DefaultPlacement, Placement, PlacementPolicy
from .task import InferenceTask
from .tiers import Tier, TierSet, TierState

__all__ = [
    "Tier", "TierState", "TierSet",
    "InferenceTask",
    "PlacementPolicy", "DefaultPlacement", "Placement",
    "resolve", "Resolution", "PlacementError", "PlacementDeferred",
]
