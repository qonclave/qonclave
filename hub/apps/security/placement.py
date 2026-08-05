"""
placement.py — where this app's inference runs.

The security app's answer to the question `framework/docs/PLACEMENT.md` poses:
given a task the application has DECLARED intent for, and tier state the
framework has MEASURED, which tier runs it?

Today's deployment is a single laptop, so the honest answer is almost always
"here". That is not a reason to skip the mechanism — the value is that when a
compute node appears, this file changes and no other does. The alternative is
what the edge had until now: the decision compiled into an `if` at the call
site, invisible and untestable.

Spec: framework/sdk/python/src/qonclave/placement/policy.py
"""

from __future__ import annotations

import logging

from qonclave.core.enums import Complexity, Privacy, Urgency
from qonclave.placement import Placement, PlacementPolicy, Tier
from qonclave.placement.task import InferenceTask
from qonclave.placement.tiers import TierSet

log = logging.getLogger("qonclave.hub")


class SecurityPlacement(PlacementPolicy):
    """Person-verification placement.

    Four rules, in the order they are checked. Each one exists because skipping
    it produces a specific wrong behaviour, noted alongside.
    """

    name = "security"

    def __init__(self, *, min_budget_ms: int = 250) -> None:
        self.min_budget_ms = min_budget_ms
        """Below this, a VLM call cannot finish in time and is not worth
        starting. Verifying a frame after the answer stopped being useful costs
        the same as verifying it usefully."""

    def decide(self, task: InferenceTask, tiers: TierSet) -> Placement:
        d = task.descriptor

        # 1. A frame of a person is personal data. It must never reach shared
        #    multi-tenant compute, whatever the rest of this method decides.
        #    The framework enforces the denial independently; stating it here
        #    makes the intent visible rather than implicit.
        if d.privacy is Privacy.NO_EGRESS:
            return Placement(Tier.HUB, deny=[Tier.COMPUTE],
                             reason="frames are personal data; never shared compute")

        # 2. Out of budget. Skipping this means spending three seconds of VLM on
        #    an answer whose deadline already passed.
        remaining = d.remaining_ms
        if remaining is not None and remaining < self.min_budget_ms:
            return Placement(Tier.EDGE, on_miss="degrade",
                             reason=f"only {remaining}ms left; too late to verify")

        # 3. Heavy reasoning prefers compute when one exists, falling back here.
        #    With no compute node the fallback is the whole behaviour, which is
        #    exactly today's single-laptop deployment.
        if d.complexity >= Complexity.VLM_REASON:
            return Placement(Tier.COMPUTE, fallback=[Tier.HUB],
                             reason="VLM reasoning; prefer compute, fall back to hub")

        # 4. Anything cheaper stays where it landed.
        return Placement(Tier.HUB, reason="cheap enough to run locally")


def task_from_event(event, *, task_id: str) -> InferenceTask:
    """Build an InferenceTask from an inbound EdgeEvent.

    The event may carry a `task` descriptor the edge declared, including the
    budget it has already spent. When it does not — which is every event from a
    device that has not been reflashed — the defaults apply and nothing has a
    deadline, so the budget rule above never fires. That is deliberate: this
    must not change behaviour for a device that knows nothing about it.
    """
    declared = event.task
    if declared is None:
        return InferenceTask.declare(
            task_id, complexity=Complexity.VLM_REASON,
            use_case="person_verification", image_path=None,
        )

    task = InferenceTask.declare(
        task_id,
        complexity=declared.complexity,
        urgency=declared.urgency,
        privacy=declared.privacy,
        use_case=declared.use_case or "person_verification",
        deadline_ms=declared.deadline_ms,
    )
    # declare() seeds remaining from deadline; the edge already spent some of it.
    if declared.remaining_ms is not None:
        task.descriptor.remaining_ms = declared.remaining_ms
    task.descriptor.hops = list(declared.hops or [])
    return task
