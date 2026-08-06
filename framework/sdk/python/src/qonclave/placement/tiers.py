"""
tiers.py — the rungs of the placement ladder and the measured state of each.

ARCHITECTURE.md already names these tiers: "Triage (Tier 1 AI)" on the edge, "Heavy Lifting
(Tier 2+ AI)" on compute. This module makes them addressable at runtime rather than being a
property of where code happens to be deployed.

`TierState` holds MEASURED facts only. Nothing an application declares appears here — that lives
in the task descriptor. Keeping the two apart is what makes placement auditable: when an event
lands somewhere surprising, you can point at either the measurement or the decision, and they are
different bugs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from ..core.models import Capabilities, Load, Power


class Tier(IntEnum):
    """A rung of the ladder.

    IntEnum so `lowest tier that satisfies` is expressible as a comparison. Ordering is by
    distance from the sensor, which is also increasing order of latency, energy cost per task,
    and blast radius if the node is compromised.
    """

    EDGE = 0
    HUB = 1
    COMPUTE = 2

    @property
    def wire(self) -> str:
        return self.name.lower()

    @classmethod
    def from_wire(cls, value: str) -> "Tier":
        return cls[value.upper()]


@dataclass(slots=True)
class TierState:
    """Live, measured state of one candidate node at one tier.

    A tier may have several candidates — notably HUB, where the home hub and any peer hub the
    device holds a valid grant for are both candidates. That is the whole of what cross-hub
    federation adds to placement: entries in this list, not a new rung.
    """

    tier: Tier
    node_id: str
    reachable: bool = True
    is_local: bool = False
    """True for the node running this decision. Exactly one candidate should be local."""

    is_peer: bool = False
    """True for a HUB candidate that is not this device's home hub — reachable only because a
    capability grant authorizes it."""

    rtt_ms: float | None = None
    power: Power = field(default_factory=Power)
    load: Load = field(default_factory=Load)
    capabilities: Capabilities = field(default_factory=Capabilities)
    multi_tenant: bool = False
    """True if this node serves several tenants. Disqualifies it from `no_egress` work."""

    def can_serve(self, complexity) -> bool:
        return self.reachable and self.capabilities.can_serve(complexity)

    @property
    def busy(self) -> bool:
        cpu = self.load.cpu_percent
        return cpu is not None and cpu > 90.0


@dataclass(slots=True)
class TierSet:
    """Everything known about where a task could run, at the moment of deciding.

    This is what a `PlacementPolicy` receives. It is a snapshot: values were true when probed and
    may be stale by the time work actually dispatches, which is why the ladder re-checks
    reachability rather than trusting the decision blindly.
    """

    candidates: list[TierState] = field(default_factory=list)

    def at(self, tier: Tier) -> list[TierState]:
        return [c for c in self.candidates if c.tier == tier]

    def best(self, tier: Tier) -> TierState | None:
        """Least-loaded reachable candidate at a tier, preferring the home hub over a peer.

        Preferring home is not about trust — a peer's grant was already verified before it entered
        this set. It is about blast radius and latency: the home hub is the one that holds this
        device's state.
        """
        options = [c for c in self.at(tier) if c.reachable]
        if not options:
            return None
        return min(options, key=lambda c: (c.is_peer, c.load.cpu_percent or 0.0, c.rtt_ms or 0.0))

    @property
    def local(self) -> TierState:
        """The node making this decision.

        Raises if absent — a node that cannot describe itself has a broken probe, and silently
        returning a placeholder would turn that into mysterious routing behavior later.
        """
        for c in self.candidates:
            if c.is_local:
                return c
        raise LookupError("TierSet has no local candidate; placement.probe did not run")

    @property
    def edge(self) -> TierState | None:
        return self.best(Tier.EDGE)

    @property
    def hub(self) -> TierState | None:
        return self.best(Tier.HUB)

    @property
    def compute(self) -> TierState | None:
        return self.best(Tier.COMPUTE)

    def reachable_tiers(self) -> list[Tier]:
        return sorted({c.tier for c in self.candidates if c.reachable})
