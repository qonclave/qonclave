"""
test_placement.py — the mechanism, not the thresholds.

The developer's thresholds are their business. What must hold regardless of policy is that the
framework measured the facts, walked the fallback chain when the chosen tier was unavailable, and
enforced privacy denials even when the policy asked for something else.

That last one is the important test in this file. `Privacy.NO_EGRESS` and `LOCAL_ONLY` produce
framework-level denials, so a policy returning COMPUTE for a no-egress task is corrected rather
than obeyed. SECURITY.md §2 states tenant isolation as a guarantee, and a guarantee that depends
on every application author remembering it is not one.
"""

from __future__ import annotations

import pytest

from qonclave.core.enums import Complexity, Privacy, Urgency
from qonclave.core.models import Capabilities, Load, Power
from qonclave.placement import (
    DefaultPlacement,
    InferenceTask,
    Placement,
    PlacementDeferred,
    PlacementError,
    PlacementPolicy,
    Tier,
    TierSet,
    TierState,
)
from qonclave.placement.ladder import resolve


def state(tier, node_id, **kw) -> TierState:
    kw.setdefault("capabilities", Capabilities(max_complexity=Complexity.LLM_REASON))
    return TierState(tier=tier, node_id=node_id, **kw)


def mesh(*, compute=True, multi_tenant=True, hub=True, peer=False) -> TierSet:
    """A full mesh, minus whatever the test removes."""
    nodes = [state(Tier.EDGE, "edge-1", is_local=True, power=Power(on_mains=True))]
    if hub:
        nodes.append(state(Tier.HUB, "hub-alpha", load=Load(cpu_percent=10.0), rtt_ms=5.0))
    if peer:
        nodes.append(
            state(Tier.HUB, "hub-beta", is_peer=True, load=Load(cpu_percent=5.0), rtt_ms=20.0)
        )
    if compute:
        nodes.append(state(Tier.COMPUTE, "npu-1", multi_tenant=multi_tenant, rtt_ms=15.0))
    return TierSet(candidates=nodes)


class Fixed(PlacementPolicy):
    """A policy that always asks for one tier, so tests exercise the mechanism not the heuristic."""

    def __init__(self, tier, **kw):
        self._placement = Placement(tier, **kw)

    def decide(self, task, tiers):
        return self._placement


# ------------------------------------------------------------------ fallback


def test_fallback_when_chosen_tier_is_absent() -> None:
    task = InferenceTask.declare("t1", complexity=Complexity.VLM_REASON)
    policy = Fixed(Tier.COMPUTE, fallback=[Tier.HUB, Tier.EDGE])

    res = resolve(task, mesh(compute=False), policy)

    assert res.tier is Tier.HUB
    assert res.fallback_applied is True
    assert res.requested is Tier.COMPUTE


def test_no_fallback_means_failure_not_substitution() -> None:
    """An empty fallback chain is a real choice: running somewhere else can be worse than not
    running at all."""
    task = InferenceTask.declare("t2", complexity=Complexity.VLM_REASON)

    with pytest.raises(PlacementError):
        resolve(task, mesh(compute=False), Fixed(Tier.COMPUTE))


def test_capability_prunes_candidates() -> None:
    """A node that cannot serve the complexity is skipped even though it is reachable."""
    tiers = mesh(compute=False)
    for c in tiers.candidates:
        c.capabilities = Capabilities(max_complexity=Complexity.DETECT)

    task = InferenceTask.declare("t3", complexity=Complexity.VLM_REASON)

    with pytest.raises(PlacementError, match="cannot_serve|no permitted tier"):
        resolve(task, tiers, Fixed(Tier.EDGE, fallback=[Tier.HUB]))


# ------------------------------------------------------------------- privacy


def test_no_egress_denies_shared_compute_even_when_policy_asks_for_it() -> None:
    """The framework overrides the policy. This is the isolation guarantee being enforced."""
    task = InferenceTask.declare("t4", privacy=Privacy.NO_EGRESS)
    policy = Fixed(Tier.COMPUTE, fallback=[Tier.HUB])

    res = resolve(task, mesh(multi_tenant=True), policy)

    assert res.tier is Tier.HUB, "a no_egress task reached a shared multi-tenant compute node"
    assert Tier.COMPUTE in res.denied


def test_no_egress_permits_single_tenant_compute() -> None:
    """Not over-strict: a dedicated compute node is not an egress risk, and blanket-denying it
    would push work back to the hub for no privacy gain."""
    task = InferenceTask.declare("t5", privacy=Privacy.NO_EGRESS)

    res = resolve(task, mesh(multi_tenant=False), Fixed(Tier.COMPUTE))

    assert res.tier is Tier.COMPUTE


def test_local_only_never_leaves_the_device() -> None:
    task = InferenceTask.declare("t6", privacy=Privacy.LOCAL_ONLY)
    policy = Fixed(Tier.COMPUTE, fallback=[Tier.HUB], on_miss="degrade")

    res = resolve(task, mesh(), policy)

    assert res.tier is Tier.EDGE
    assert res.node.is_local
    assert {Tier.HUB, Tier.COMPUTE} <= set(res.denied)


# -------------------------------------------------------------------- budget


def test_expired_budget_is_refused_before_any_work() -> None:
    task = InferenceTask.declare("t7", deadline_ms=100)
    task.descriptor.remaining_ms = 0

    with pytest.raises(PlacementError, match="no budget"):
        resolve(task, mesh(), Fixed(Tier.HUB))


def test_escalation_deducts_the_budget() -> None:
    """Without this the next tier plans against a budget that is already spent — a silent failure
    that only surfaces as missed deadlines under load."""
    task = InferenceTask.declare("t8", deadline_ms=200)
    task.started_at -= 0.140  # pretend 140ms elapsed locally

    descriptor = task.for_escalation("edge")

    assert descriptor.remaining_ms is not None and descriptor.remaining_ms <= 60
    assert descriptor.hops == ["edge"]


# -------------------------------------------------------------- miss handling


def test_degrade_falls_back_to_the_local_node() -> None:
    task = InferenceTask.declare("t9", complexity=Complexity.HEURISTIC)
    policy = Fixed(Tier.COMPUTE, on_miss="degrade")

    res = resolve(task, mesh(compute=False, hub=False), policy)

    assert res.degraded is True
    assert res.node.is_local


def test_defer_is_a_distinct_signal() -> None:
    """On a duty-cycled device "retry later" may mean tomorrow, so it must not be collapsed into
    a generic failure."""
    task = InferenceTask.declare("t10")

    with pytest.raises(PlacementDeferred):
        resolve(task, mesh(compute=False, hub=False), Fixed(Tier.HUB, on_miss="defer"))


# ---------------------------------------------------------------- peer hubs


def test_peer_hub_is_just_another_candidate() -> None:
    """Federation adds entries to the candidate list, not a rung to the ladder."""
    task = InferenceTask.declare("t11")

    res = resolve(task, mesh(peer=True), Fixed(Tier.HUB, prefer="peer"))

    assert res.tier is Tier.HUB
    assert res.node.node_id == "hub-beta"
    assert res.node.is_peer


def test_prefer_is_a_hint_not_a_constraint() -> None:
    """If a policy asks for a peer and none is authorized, the home hub is still correct —
    treating the hint as binding would turn a load optimization into an outage."""
    task = InferenceTask.declare("t12")

    res = resolve(task, mesh(peer=False), Fixed(Tier.HUB, prefer="peer"))

    assert res.node.node_id == "hub-alpha"


def test_home_hub_preferred_when_unhinted() -> None:
    task = InferenceTask.declare("t13")
    tiers = mesh(peer=True)

    assert tiers.hub is not None and tiers.hub.node_id == "hub-alpha"


# --------------------------------------------------------------- the default


def test_default_keeps_tight_deadlines_local() -> None:
    task = InferenceTask.declare("t14", urgency=Urgency.CRITICAL, deadline_ms=30)

    res = resolve(task, mesh(), DefaultPlacement())

    assert res.tier is Tier.EDGE


def test_default_escalates_off_a_depleting_battery() -> None:
    tiers = mesh()
    tiers.local.power = Power(battery_pct=15.0, on_mains=False)

    task = InferenceTask.declare("t15", complexity=Complexity.CLASSIFY)
    res = resolve(task, tiers, DefaultPlacement())

    assert res.tier is not Tier.EDGE


def test_default_stays_local_on_mains_despite_low_battery_reading() -> None:
    """A charging node is not energy-limited, whatever its battery says."""
    tiers = mesh()
    tiers.local.power = Power(battery_pct=5.0, on_mains=True)

    task = InferenceTask.declare("t16", complexity=Complexity.HEURISTIC)
    res = resolve(task, tiers, DefaultPlacement())

    assert res.tier is Tier.EDGE


def test_default_sends_vlm_work_to_compute() -> None:
    task = InferenceTask.declare("t17", complexity=Complexity.VLM_REASON)

    res = resolve(task, mesh(), DefaultPlacement())

    assert res.tier is Tier.COMPUTE


def test_default_still_works_with_no_compute_node() -> None:
    """The monolith case from DEPLOYMENT.md Topology A/B — a supported production shape."""
    task = InferenceTask.declare("t18", complexity=Complexity.VLM_REASON)

    res = resolve(task, mesh(compute=False), DefaultPlacement())

    assert res.tier is Tier.HUB
    assert res.fallback_applied is True


def test_tierset_without_local_candidate_is_an_error() -> None:
    """A node that cannot describe itself has a broken probe. Returning a placeholder would turn
    that into mysterious routing behavior much later."""
    tiers = TierSet(candidates=[state(Tier.HUB, "hub-alpha")])

    with pytest.raises(LookupError, match="no local candidate"):
        _ = tiers.local
