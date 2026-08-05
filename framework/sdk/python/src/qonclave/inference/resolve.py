"""
resolve.py — turn a task into the backend that will run it.

This is the seam that makes the Compute role optional. A caller does:

    backend, resolution = resolve(task, tiers, policy, backends)
    result = backend.infer(...)

and never learns whether it got a local model or a compute node across the network. Placement
decides the tier; this maps that tier onto something with an `infer()` method.

The layering matters. `resolve` imports `placement` (below it) and the local backends (beside it),
but never `qonclave.compute` — that is a role package, and importing it here would make a hub
doing its own inference depend on the optional server it is supposed to be able to do without.
"""

from __future__ import annotations

import logging

from ..placement.ladder import Resolution, resolve as resolve_tier
from ..placement.policy import PlacementPolicy
from ..placement.task import InferenceTask
from ..placement.tiers import Tier, TierSet
from .backend import ModelBackend

log = logging.getLogger("qonclave.inference")


def resolve(
    task: InferenceTask,
    tiers: TierSet,
    policy: PlacementPolicy,
    backends: dict[Tier, ModelBackend],
) -> tuple[ModelBackend, Resolution]:
    """Choose where the task runs and return the backend that runs it.

    `backends` maps each tier this node can dispatch to onto a ModelBackend. A local tier maps to
    a local backend; a remote tier maps to a RemoteBackend wrapping the transport. Both satisfy
    the same ABC, which is the point.

    Raises PlacementError / PlacementDeferred from the ladder — those are meaningfully different
    outcomes and should not be collapsed into one exception here.
    """
    resolution = resolve_tier(task, tiers, policy)

    backend = backends.get(resolution.tier)
    if backend is None:
        raise LookupError(
            f"placement chose tier {resolution.tier.wire} but no backend is registered for it; "
            f"registered: {[t.wire for t in backends]}"
        )

    log.debug("task %s -> %s", task.task_id, resolution.explain())
    return backend, resolution


def local_only(backend: ModelBackend) -> dict[Tier, ModelBackend]:
    """Backend map for a node with no peers — the monolith case.

    DEPLOYMENT.md Topology A/B: one laptop running everything. A supported production shape, not
    just a development mode, so it deserves a named constructor rather than being assembled by
    hand at every call site.
    """
    return {Tier.EDGE: backend, Tier.HUB: backend}
