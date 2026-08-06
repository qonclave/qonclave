"""
ladder.py — the mechanism around the decision.

The developer's `PlacementPolicy` says where it *wants* work to run. This module is what actually
happens: privacy denials are enforced, the chosen tier is checked for reachability and capability,
the fallback chain is walked, and the outcome is recorded.

The important property is that a policy cannot break isolation. `Privacy.NO_EGRESS` and
`LOCAL_ONLY` produce framework-level denials that are unioned with whatever the policy asked for,
so a policy returning COMPUTE for a no-egress task is corrected rather than obeyed. SECURITY.md §2
asserts tenant isolation as a guarantee; a guarantee that depends on every app author remembering
it is not one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..core.enums import Privacy
from .policy import Placement, PlacementPolicy
from .task import InferenceTask
from .tiers import Tier, TierSet, TierState

log = logging.getLogger("qonclave.placement")


class PlacementError(RuntimeError):
    """No permitted tier could serve the task and `on_miss` was `fail`."""


class PlacementDeferred(RuntimeError):
    """No tier available now; `on_miss` was `defer`. The caller should spool and retry.

    On a duty-cycled device "retry" may mean tomorrow, which is why this is a distinct signal
    rather than a generic failure.
    """


@dataclass(slots=True)
class Resolution:
    """The outcome. Everything `qonclave placement-explain` prints comes from here."""

    tier: Tier
    node: TierState
    requested: Tier
    fallback_applied: bool = False
    degraded: bool = False
    denied: list[Tier] = field(default_factory=list)
    considered: list[str] = field(default_factory=list)
    reason: str = ""

    def explain(self) -> str:
        bits = [f"tier={self.tier.wire}", f"node={self.node.node_id}"]
        if self.fallback_applied:
            bits.append(f"fallback_applied=true(requested={self.requested.wire})")
        if self.degraded:
            bits.append("degraded=true")
        if self.denied:
            bits.append(f"denied=[{','.join(t.wire for t in self.denied)}]")
        if self.reason:
            bits.append(f'reason="{self.reason}"')
        return " ".join(bits)


def privacy_denials(privacy: Privacy, tiers: TierSet) -> list[Tier]:
    """Tiers the framework forbids for this task, independent of any policy.

    `NO_EGRESS` denies only *shared* compute. A single-tenant compute node is not an egress risk,
    and blanket-denying it would push work back onto the hub for no privacy gain — the kind of
    over-strict rule that gets disabled in production and takes the real protection with it.
    """
    if privacy is Privacy.LOCAL_ONLY:
        return [Tier.HUB, Tier.COMPUTE]
    if privacy is Privacy.NO_EGRESS:
        return [c.tier for c in tiers.at(Tier.COMPUTE) if c.multi_tenant] or []
    return []


def resolve(
    task: InferenceTask,
    tiers: TierSet,
    policy: PlacementPolicy,
) -> Resolution:
    """Run the placement mechanism end to end and return where the task should execute."""
    descriptor = task.descriptor

    if descriptor.expired:
        raise PlacementError(f"task {task.task_id} arrived with no budget left")

    decision: Placement = policy.decide(task, tiers)

    denied = sorted(set(decision.deny) | set(privacy_denials(descriptor.privacy, tiers)))
    if decision.tier in denied:
        log.debug(
            "policy %s chose %s for task %s but it is denied (%s); falling back",
            policy.name, decision.tier.wire, task.task_id, descriptor.privacy,
        )

    considered: list[str] = []
    chain = [decision.tier, *decision.fallback]

    for candidate_tier in chain:
        if candidate_tier in denied:
            considered.append(f"{candidate_tier.wire}:denied")
            continue

        node = _pick(tiers, candidate_tier, decision.prefer)
        if node is None:
            considered.append(f"{candidate_tier.wire}:unreachable")
            continue
        if not node.can_serve(descriptor.complexity):
            considered.append(f"{candidate_tier.wire}:cannot_serve")
            continue

        return Resolution(
            tier=candidate_tier,
            node=node,
            requested=decision.tier,
            fallback_applied=candidate_tier != decision.tier,
            denied=denied,
            considered=considered,
            reason=decision.reason,
        )

    return _handle_miss(task, tiers, decision, denied, considered)


def _pick(tiers: TierSet, tier: Tier, prefer: str | None) -> TierState | None:
    """Choose among candidates at one tier.

    `prefer` is a hint, not a constraint: if a policy asks for a peer hub and none is authorized,
    the home hub is still a correct answer. Treating the hint as binding would turn a load
    optimization into an outage.
    """
    options = [c for c in tiers.at(tier) if c.reachable]
    if not options:
        return None
    if prefer == "peer":
        peers = [c for c in options if c.is_peer]
        if peers:
            options = peers
    elif prefer == "home":
        home = [c for c in options if not c.is_peer]
        if home:
            options = home
    return min(options, key=lambda c: (c.load.cpu_percent or 0.0, c.rtt_ms or 0.0))


def _handle_miss(
    task: InferenceTask,
    tiers: TierSet,
    decision: Placement,
    denied: list[Tier],
    considered: list[str],
) -> Resolution:
    if decision.on_miss == "degrade":
        local = tiers.local
        if local.reachable and Tier.EDGE not in denied:
            log.info("task %s degrading to local model on %s", task.task_id, local.node_id)
            return Resolution(
                tier=local.tier,
                node=local,
                requested=decision.tier,
                fallback_applied=True,
                degraded=True,
                denied=denied,
                considered=considered,
                reason="degraded: no permitted tier could serve",
            )

    if decision.on_miss == "defer":
        raise PlacementDeferred(
            f"task {task.task_id}: no tier available (considered {considered}); spool and retry"
        )

    raise PlacementError(
        f"task {task.task_id}: no permitted tier could serve it "
        f"(considered {considered}, denied {[t.wire for t in denied]})"
    )
