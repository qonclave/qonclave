"""
policy.py — the placement contract a developer implements.

There is deliberately no rule DSL and no ruleset schema. The developer writes code, in the same
idiom as `hub.Policy`, so there is one thing to learn rather than two. What the framework owns is
everything *around* the decision:

    framework owns                          developer owns
    ---------------------------------       -------------------------
    measuring power / thermal / load / rtt   the decision
    walking the fallback chain               the thresholds
    enforcing `deny` (see below)             which metrics matter
    deducting the deadline across hops
    dispatching to the resolved backend

`deny` is enforced by the ladder, not trusted to the policy. A policy that returns COMPUTE for a
`no_egress` task is overridden — the isolation guarantee in SECURITY.md §2 must not depend on
every application author remembering it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..core.enums import Complexity, Privacy, Urgency
from .task import InferenceTask
from .tiers import Tier, TierSet


@dataclass(slots=True)
class Placement:
    """A placement decision.

    `tier` is where the developer wants the work to run. Everything else describes what should
    happen when that turns out to be impossible — which is the common case in a mesh where nodes
    sleep, throttle, and disappear.
    """

    tier: Tier

    fallback: list[Tier] = field(default_factory=list)
    """Tiers to try, in order, if `tier` is unreachable or cannot serve the task. Empty means
    fail rather than substitute — the right choice when running somewhere else would be worse
    than not running at all."""

    deny: list[Tier] = field(default_factory=list)
    """Tiers this task must never reach. Merged with the framework's own privacy-derived denials;
    the union is enforced."""

    on_miss: str = "fail"
    """What to do when no permitted tier can serve the task.

    fail     — raise. The caller decides.
    degrade  — run locally with a smaller model. For latency-critical work where a late correct
               answer is worth less than a fast approximate one.
    defer    — spool and retry later. For duty-cycled devices, "later" may be tomorrow.
    """

    prefer: str | None = None
    """Optional hint among candidates at the chosen tier: "peer" or "home". Ignored if no such
    candidate exists."""

    reason: str = ""
    """Free text, surfaced by `qonclave placement-explain`. Worth setting — it is the difference
    between debugging a placement and guessing at it."""


class PlacementPolicy(ABC):
    """Decide which tier runs a task.

    Subclass and implement `decide`. The framework calls it once per inference task, having
    already pruned candidates that cannot serve the task's complexity.
    """

    name: str = "placement"

    @abstractmethod
    def decide(self, task: InferenceTask, tiers: TierSet) -> Placement:
        """Choose a tier for one task.

        `tiers` is a snapshot of measured state — `.local`, `.edge`, `.hub`, `.compute`, each
        carrying power, load, rtt, and capabilities. `task` is what the application declared:
        complexity, urgency, privacy, deadline, use_case.

        Return a `Placement`. Do not raise for "cannot run here" — return a tier with a fallback
        chain and let the ladder work it out.
        """
        raise NotImplementedError


class DefaultPlacement(PlacementPolicy):
    """Sensible behavior for an app that has not thought about placement yet.

    The rules encoded here are the ones that are nearly always right:

    1. Honor privacy before anything else.
    2. Very tight deadlines stay local — a network hop alone can exceed the budget.
    3. An energy-limited node hands work up rather than spinning its own accelerator.
    4. Otherwise run at the lowest tier that can actually serve the task.

    A real deployment should subclass `PlacementPolicy` instead. This exists so that not doing so
    yields something defensible rather than something arbitrary.
    """

    name = "default"

    #: Below this, a round trip is a material fraction of the budget.
    LOCAL_ONLY_DEADLINE_MS = 50

    def decide(self, task: InferenceTask, tiers: TierSet) -> Placement:
        d = task.descriptor

        if d.privacy is Privacy.LOCAL_ONLY:
            return Placement(
                Tier.EDGE,
                deny=[Tier.HUB, Tier.COMPUTE],
                on_miss="degrade",
                reason="privacy=local_only",
            )

        if d.privacy is Privacy.NO_EGRESS:
            return Placement(
                Tier.HUB,
                fallback=[Tier.EDGE],
                deny=[Tier.COMPUTE],
                reason="privacy=no_egress: no shared multi-tenant node",
            )

        if d.deadline_ms is not None and d.deadline_ms < self.LOCAL_ONLY_DEADLINE_MS:
            return Placement(
                Tier.EDGE,
                on_miss="degrade",
                reason=f"deadline {d.deadline_ms}ms leaves no room for a hop",
            )

        local = tiers.local
        if local.power.is_constrained or local.power.is_throttling:
            return Placement(
                Tier.HUB,
                fallback=[Tier.COMPUTE],
                reason="local node is energy-limited or throttling",
            )

        if d.complexity >= Complexity.VLM_REASON:
            return Placement(
                Tier.COMPUTE,
                fallback=[Tier.HUB, Tier.EDGE],
                reason=f"complexity={d.complexity.wire} wants a real accelerator",
            )

        if d.urgency >= Urgency.HIGH and local.can_serve(d.complexity):
            return Placement(Tier.EDGE, fallback=[Tier.HUB], reason="high urgency, local can serve")

        for tier in (Tier.EDGE, Tier.HUB, Tier.COMPUTE):
            best = tiers.best(tier)
            if best is not None and best.can_serve(d.complexity) and not best.busy:
                remaining = [t for t in (Tier.EDGE, Tier.HUB, Tier.COMPUTE) if t > tier]
                return Placement(tier, fallback=remaining, reason="lowest tier that can serve")

        return Placement(
            Tier.HUB,
            fallback=[Tier.COMPUTE, Tier.EDGE],
            on_miss="defer",
            reason="no unloaded tier could serve; deferring",
        )
