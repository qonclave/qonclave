"""
probe.py — measure the facts a placement decision runs on.

The declared/measured split is the discipline this whole layer rests on. An application declares
intent (complexity, urgency, privacy, deadline); this module supplies fact (power, thermal, load,
rtt, capability). A `PlacementPolicy` sees both and is responsible for neither's collection.

Measurement is best-effort and MUST NOT raise. A hub that cannot read its own battery still has
to place work — degrading to "unknown" is correct, crashing is not.
"""

from __future__ import annotations

import logging
import os
import shutil

from ..core.models import Capabilities, Load, NodeManifest, Power
from .tiers import Tier, TierSet, TierState

log = logging.getLogger("qonclave.placement.probe")

_TYPE_TO_TIER = {"edge": Tier.EDGE, "hub": Tier.HUB, "compute": Tier.COMPUTE}


def local_power() -> Power:
    """Best-effort local power state.

    Uses psutil when present. Its absence is expected rather than exceptional — an edge install
    deliberately carries almost no dependencies — so the fallback assumes mains power, which is
    the safe default: it makes placement willing to do local work rather than needlessly
    escalating everything.
    """
    try:
        import psutil  # type: ignore

        battery = psutil.sensors_battery()
        if battery is not None:
            return Power(battery_pct=float(battery.percent), on_mains=bool(battery.power_plugged))
    except Exception:
        pass
    return Power(on_mains=True)


def local_load() -> Load:
    try:
        import psutil  # type: ignore

        return Load(cpu_percent=float(psutil.cpu_percent(interval=None)))
    except Exception:
        pass

    try:
        one_min, _, _ = os.getloadavg()
        cores = os.cpu_count() or 1
        return Load(cpu_percent=min(100.0, one_min / cores * 100.0))
    except (OSError, AttributeError):
        # No load average on Windows. Reporting nothing is honest; reporting 0.0 would tell
        # placement this node is idle, which is a worse answer than "unknown".
        return Load()


def local_capabilities(models: list[str] | None = None, max_complexity=None) -> Capabilities:
    hardware: list[str] = []
    import platform

    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        hardware.append("x86")
    elif machine in ("arm64", "aarch64"):
        hardware.append("arm64")

    if shutil.which("qairt") or os.environ.get("QONCLAVE_HAS_NPU") == "1":
        hardware.append("npu")

    return Capabilities(
        hardware=hardware,
        supported_models=models or [],
        max_complexity=max_complexity,
        encodings=["json", "cbor"],
    )


def local_state(node_id: str, tier: Tier, **kw) -> TierState:
    """Describe the node making the decision."""
    return TierState(
        tier=tier,
        node_id=node_id,
        is_local=True,
        reachable=True,
        rtt_ms=0.0,
        power=kw.pop("power", None) or local_power(),
        load=kw.pop("load", None) or local_load(),
        capabilities=kw.pop("capabilities", None) or local_capabilities(),
        **kw,
    )


def from_manifest(
    manifest: NodeManifest,
    *,
    rtt_ms: float | None = None,
    is_peer: bool = False,
    reachable: bool = True,
) -> TierState:
    """Build candidate state from a discovered peer's manifest.

    `is_peer` marks a HUB-tier candidate that is not this device's home hub — reachable only
    because a capability grant authorizes it. It stays a plain candidate at the same tier;
    federation adds entries to the candidate list, not a rung to the ladder.
    """
    return TierState(
        tier=_TYPE_TO_TIER.get(manifest.node_type.value, Tier.HUB),
        node_id=manifest.node_id,
        reachable=reachable,
        is_peer=is_peer,
        rtt_ms=rtt_ms,
        power=manifest.power or Power(),
        load=manifest.load,
        capabilities=manifest.capabilities,
        multi_tenant=manifest.accepts_foreign_edges,
    )


def build(local: TierState, peers: list[TierState] | None = None) -> TierSet:
    """Assemble the snapshot handed to a PlacementPolicy."""
    return TierSet(candidates=[local, *(peers or [])])
